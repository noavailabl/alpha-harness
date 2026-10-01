"""Calling the model.

Every call goes through :meth:`LLMService.generate`, which does the same four things in
the same order: pick a key with budget, send, record what it actually cost, and — when
Google disagrees with our accounting — mark that pair spent and try the next key rather
than retrying into the same wall.

Rotation chooses by remaining daily budget and believes a ``429`` immediately, because a
retry loop that tries each key in turn burns a request from every key on a bad day.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx2
import structlog
from openai import DefaultAsyncHttpxClient

from ..schemas import camel_dict
from .budget import Ledger
from .claude_cli import ClaudeCLI
from .codex_cli import DEFAULT_MODEL_ID as CODEX_DEFAULT_MODEL
from .codex_cli import LEGACY_MODEL_ID, CodexCLI
from .keys import BudgetExhaustedError, KeyStore, LLMError
from .openai_compat import OpenAICompatible, ProviderError
from .providers import PROVIDERS
from .registry import DEFAULT_MODEL, ModelInfo, ModelRegistry
from .text import estimate_tokens

if TYPE_CHECKING:
    from openai.types.shared import ReasoningEffort

    from ..db.sqlite import Database
    from ..sealing import Sealer

log = structlog.get_logger(__name__)

#: How long a single generation may take before we give up on it.
TIMEOUT_SECONDS = 180.0


@dataclass(slots=True)
class Answer:
    """One model response, and the tokens it cost."""

    text: str
    model: str
    prompt_tokens: int
    output_tokens: int
    thinking_tokens: int
    total_tokens: int

    @property
    def usage(self) -> dict[str, int]:
        return camel_dict(self, exclude=("text", "model"))


def _status(exc: Exception) -> int | None:
    return exc.status if isinstance(exc, ProviderError) else None


def _is_rate_limit(exc: Exception) -> tuple[bool, bool]:
    """``(rate limited, daily)``. Only the provider's details say which limit was hit."""
    text = str(exc).lower()
    limited = _status(exc) == 429 or "resource_exhausted" in text or "quota" in text
    daily = "per day" in text or "perday" in text or "daily" in text or "requests per day" in text
    return limited, daily


def _plain_reason(exc: Exception) -> str | None:
    """The common failures, said in words the person reading them can act on.

    Google's errors arrive as a wall of JSON; the raw text is still kept against the key as
    ``lastError`` for anyone who wants it.
    """
    text, status = str(exc).lower(), _status(exc)
    if "api_key_invalid" in text or "api key not valid" in text or "unauthenticated" in text:
        return (
            "That Google AI Studio key was not accepted. Check it was copied whole, and "
            "that it has not been deleted at aistudio.google.com."
        )
    if status == 403 or "permission_denied" in text:
        return (
            "Google refused that key. It may not have access to this model, or the "
            "project it belongs to may have been closed."
        )
    if status == 404 or ("not found" in text and "model" in text):
        return "That model is not available to this key. Pick a different one."
    if status == 503:
        return (
            "That model is overloaded right now, which usually passes within a minute. "
            "Try again, or pick another model."
        )
    return None


def _refused(exc: Exception) -> bool:
    """Whether the key was turned away rather than the request: another key may get in."""
    return _status(exc) in (401, 403) or "api_key_invalid" in str(exc).lower()


#: How long a connection to a provider stays open between calls: a fresh one costs about
#: 0.4 s of set-up, measured against Google, on answers that take two or three seconds.
KEEPALIVE_SECONDS = 60.0

#: Models that reject ``minimal``: "not supported for Gemini 3.8 Flash" per Google's docs.
_NO_MINIMAL = ("gemini-3.7", "gemini-3.8")

_EFFORTS: dict[str, ReasoningEffort] = {
    "MINIMAL": "minimal",
    "LOW": "low",
    "MEDIUM": "medium",
    "HIGH": "high",
}


def _effort(model: ModelInfo, thinking: str | None) -> ReasoningEffort:
    """Callers ask for a thinking level; this is the ``reasoning_effort`` the model accepts.

    Only Gemini 3 takes one, and 3.7 and 3.8 refuse ``minimal`` with a 400 on every message.
    """
    if not thinking or model.provider != "google" or not model.id.startswith("gemini-3"):
        return None
    effort = _EFFORTS[thinking.upper()]
    return "low" if effort == "minimal" and model.id.startswith(_NO_MINIMAL) else effort


class LLMService:
    """The assistant: keys, budget and models in one place."""

    def __init__(self, db: Database, sealer: Sealer, registry: ModelRegistry) -> None:
        self.db = db
        self.registry = registry
        self.ledger = Ledger(db)
        self.keys = KeyStore(db, sealer, self.ledger)
        self.codex = CodexCLI()
        self.claude = ClaudeCLI()
        self._http = DefaultAsyncHttpxClient(
            timeout=TIMEOUT_SECONDS, limits=httpx2.Limits(keepalive_expiry=KEEPALIVE_SECONDS)
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    # -- plumbing --------------------------------------------------------

    def model_for(self, model_id: str | None) -> ModelInfo | None:
        """Resolve saved model ids, including the first Codex integration's alias."""
        resolved = CODEX_DEFAULT_MODEL if model_id == LEGACY_MODEL_ID else model_id or DEFAULT_MODEL
        return self.registry.get(resolved)

    def _client(self, provider: str, secret: str) -> OpenAICompatible:
        # Not providers.get: its fallback to Google would send another provider's key there.
        spec = PROVIDERS.get(provider)
        if spec is None:
            raise LLMError(f"{provider!r} is not a provider this application knows.")
        return OpenAICompatible(spec.base_url, secret, self._http)

    # -- the one call ----------------------------------------------------

    async def generate(
        self,
        *,
        system: str,
        user: str,
        model_id: str | None,
        response_schema: dict[str, Any],
        temperature: float,
        thinking: str | None = None,
    ) -> Answer:
        """Send one prompt, rotating keys by remaining budget.

        ``thinking`` is one of Google's thinking levels. Not a free upgrade: thinking tokens
        are billed against the same per-minute budget as the answer.
        """
        model = self.model_for(model_id)
        if model is None:
            raise LLMError(f"{model_id!r} is not a known model. Choose one in AI › Budget.")
        if model.provider == "codex":
            reply = await self.codex.generate(
                system,
                user,
                model=model.id,
                schema=response_schema,
            )
            return Answer(
                text=reply.text,
                model=model.id,
                prompt_tokens=reply.prompt_tokens,
                output_tokens=reply.output_tokens,
                thinking_tokens=0,
                total_tokens=reply.total_tokens,
            )
        if model.provider == "claude":
            answer = await self.claude.generate(
                system,
                user,
                model=model.id,
                schema=response_schema,
            )
            return Answer(
                text=answer.text,
                model=model.id,
                prompt_tokens=answer.prompt_tokens,
                output_tokens=answer.output_tokens,
                thinking_tokens=0,
                total_tokens=answer.total_tokens,
            )
        estimate = estimate_tokens(system) + estimate_tokens(user)
        if estimate > model.tpm:
            # No wait fits a request bigger than the whole per-minute budget: say so, rather
            # than a budget error that asks to try again in 0 seconds, forever.
            raise LLMError(
                f"This request is about {estimate:,} tokens, more than {model.label} accepts "
                f"in a minute ({model.tpm:,}). Use a model with a larger limit or ask less."
            )
        tried: set[int] = set()
        refusal: LLMError | None = None
        effort = _effort(model, thinking)
        # Google holds the answer to the schema. The others are only asked for a JSON object,
        # which not all of them honour either; the prompt names the keys it needs.
        schema = response_schema if model.provider == "google" else None

        while True:
            try:
                key_id = await self.keys.choose(model, estimated_tokens=estimate, skip=tried)
            except BudgetExhaustedError as exc:
                # Every key has been tried: one that was turned away says more than a budget.
                if refusal is not None:
                    raise refusal from None
                if tried:
                    exc.args = (f"{exc.args[0]} Already tried: {len(tried)} key(s).",)
                raise
            tried.add(key_id)

            row = await self.keys.get(key_id)
            secret = await self.keys.secret(key_id)
            client = self._client(model.provider, secret)

            try:
                text, usage = await asyncio.wait_for(
                    client.generate(
                        model=model.id,
                        system=system,
                        user=user,
                        temperature=temperature,
                        schema=schema,
                        reasoning_effort=effort,
                    ),
                    timeout=TIMEOUT_SECONDS,
                )
            except TimeoutError as exc:
                await self.keys.mark(key_id, error="Timed out")
                raise LLMError(
                    f"{model.label} did not answer within {TIMEOUT_SECONDS:.0f} seconds. "
                    "Try a smaller question or a lighter model."
                ) from exc
            except Exception as exc:
                limited, daily = _is_rate_limit(exc)
                await self.keys.mark(key_id, error=str(exc)[:300])
                if limited:
                    # Google is the authority: mark this pair spent and rotate rather than
                    # retrying into a limit we evidently mis-tracked.
                    await self.ledger.penalise(
                        key_id, model, daily=daily, cap=row.daily_limit if row else None
                    )
                    log.warning("llm.rate_limited", key_id=key_id, model=model.id, daily=daily)
                    continue
                error = LLMError(_plain_reason(exc) or f"{model.label} could not be reached: {exc}")
                if _refused(exc):
                    log.warning("llm.key_refused", key_id=key_id, model=model.id)
                    refusal = error
                    continue
                raise error from exc

            prompt = int(usage.get("prompt_tokens") or 0)
            output = int(usage.get("completion_tokens") or 0)
            reported = int(usage.get("total_tokens") or 0)
            # Google reports no breakdown: its thinking is only what the total holds beyond
            # the prompt and the answer.
            thought = int(
                (usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
            ) or max(0, reported - prompt - output)
            total = reported or estimate
            await self.ledger.record(key_id, model.id, total)
            await self.keys.mark(key_id)

            return Answer(
                text=text.strip(),
                model=model.id,
                prompt_tokens=prompt,
                output_tokens=output,
                thinking_tokens=thought,
                total_tokens=total,
            )

    # -- health ----------------------------------------------------------

    async def check_key(self, key_id: int) -> dict[str, Any]:
        """Confirm a key works, without spending a generation request.

        ``models.list`` is not billed against the generation quotas, so a health check
        costs nothing that matters.
        """
        row = await self.keys.get(key_id)
        provider = str(getattr(row, "provider", "google") or "google")
        secret = await self.keys.secret(key_id)
        try:
            names = await self._client(provider, secret).models()
        # Any provider failure is the key test's answer, stored and shown to the user.
        except Exception as exc:  # noqa: BLE001
            await self.keys.mark(key_id, error=str(exc)[:300])
            return {"keyId": key_id, "ok": False, "error": str(exc)[:300]}

        await self.keys.mark(key_id)
        added = self.registry.merge_discovered(names, provider)
        return {"keyId": key_id, "ok": True, "models": len(names), "newModels": added}
