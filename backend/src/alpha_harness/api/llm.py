"""The assistant: keys, models and the prompts it sends.

Prompts are served in full on purpose: one decides what an answer looks like and is
otherwise invisible. Keys are the only secret here, and leave only as a masked hint.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..llm.keys import serialise
from ..llm.prompts import PROMPTS
from ..llm.providers import LLMProviders, catalogue
from ..llm.registry import LLMModels
from ..llm.text import estimate_tokens
from ..schemas import Out
from .deps import State

router = APIRouter(prefix="/api/llm", tags=["assistant"])


class PromptInfo(Out):
    slug: str
    label: str
    purpose: str
    body: str
    characters: int
    estimated_tokens: int


class PromptList(Out):
    prompts: list[PromptInfo]


class LLMKeyUsage(Out):
    key_id: int
    model: str
    #: Pacific day, YYYY-MM-DD.
    day: str
    requests: int
    tokens: int
    last_request_at: str | None


class LLMKey(Out):
    id: int
    label: str
    provider: str
    hint: str
    enabled: bool
    #: The user's own daily ceiling, or null to use the model's.
    daily_limit: int | None
    last_ok_at: str | None
    last_error: str | None
    created_at: str | None
    usage: list[LLMKeyUsage]


class LLMBudget(Out):
    model: str
    label: str
    provider: str
    per_key_per_day: int
    remaining_today: int
    bulk: bool


class LLMKeyStatus(Out):
    keys: list[LLMKey]
    enabled: int
    budget: list[LLMBudget]
    reset_in_seconds: int
    quota_timezone: str


class KeyWorks(Out):
    key_id: int
    ok: Literal[True]
    #: How many models the key can reach, and those new to the roster.
    models: int
    new_models: list[str]


class KeyFailed(Out):
    key_id: int
    ok: Literal[False]
    error: str


class CodexStatus(Out):
    connected: bool
    auth: str | None
    model: str
    reasoning_effort: Literal["medium"]


def _check(result: dict[str, Any]) -> KeyWorks | KeyFailed:
    if result["ok"]:
        return KeyWorks.model_validate(result)
    return KeyFailed.model_validate(result)


# --- setup ----------------------------------------------------------------


@router.get("/models")
async def models(state: State) -> LLMModels:
    """The model roster with each one's daily budget.

    Requests-per-day is the limit that ends a session, so it travels with every entry
    rather than sitting in a help page.
    """
    return state.llm.registry.roster()


@router.get("/codex")
async def codex_status(state: State) -> CodexStatus:
    """Whether the local Codex CLI can use the signed-in ChatGPT allowance."""
    from ..llm.codex_cli import DEFAULT_MODEL_ID, MEDIUM_EFFORT

    connected = await state.llm.codex.connected()
    return CodexStatus(
        connected=connected,
        auth="ChatGPT" if connected else None,
        model=DEFAULT_MODEL_ID,
        reasoning_effort=MEDIUM_EFFORT,
    )


# --- prompts --------------------------------------------------------------


@router.get("/prompts")
async def list_prompts() -> PromptList:
    """Every system prompt the application sends, in full. The token estimate is shown
    because prompt tokens come out of the same per-minute budget as the answer."""
    return PromptList(
        prompts=[
            PromptInfo(
                slug=p.slug,
                label=p.label,
                purpose=p.purpose,
                body=p.body,
                characters=len(p.body),
                estimated_tokens=estimate_tokens(p.body),
            )
            for p in PROMPTS
        ]
    )


@router.get("/keys")
async def list_keys(state: State) -> LLMKeyStatus:
    """Keys, today's usage, and how much budget is left across all of them."""
    return LLMKeyStatus.model_validate(await state.llm.keys.status(state.llm.registry))


@router.get("/providers")
async def providers() -> LLMProviders:
    """Every assistant that can answer, and how to get a free key for it.

    All of them are free and need no card — the assistant is optional here, so asking for
    payment details would turn a convenience into a purchase decision.
    """
    return catalogue()


class AddKey(BaseModel):
    key: str = Field(description="An assistant API key. Sealed at rest; never returned.")
    label: str | None = Field(default=None, description="Which account this key belongs to")
    provider: str = Field(default="google", description="Whose key this is")
    daily_limit: int | None = Field(
        default=None,
        ge=1,
        description="Daily request ceiling for this key. Required for a paid provider.",
    )


@router.post("/keys", status_code=201)
async def add_key(body: AddKey, state: State) -> LLMKey:
    """Store a key.

    Quota is per account, so adding a key from a second account genuinely doubles the
    daily budget — which is why the same key cannot be added twice.
    """
    row = await state.llm.keys.add(
        body.key, body.label, provider=body.provider, daily_limit=body.daily_limit
    )
    return LLMKey.model_validate(serialise(row))


@router.post("/keys/{key_id}/check")
async def check_key(key_id: int, state: State) -> KeyWorks | KeyFailed:
    """Confirm a key works. Costs nothing against the generation quota."""
    return _check(await state.llm.check_key(key_id))


@router.post("/keys/check")
async def check_all_keys(state: State) -> list[KeyWorks | KeyFailed]:
    return [_check(await state.llm.check_key(k.id)) for k in await state.llm.keys.list_keys()]


class KeyToggle(BaseModel):
    enabled: bool
    daily_limit: int | None = Field(
        default=None, ge=1, description="Move this key's daily cap. Left alone when omitted."
    )
    #: Explicit rather than a zero sentinel, because omitted already means "leave it".
    clear_daily_limit: bool = Field(
        default=False, description="Remove this key's daily cap, going back to the model's."
    )


@router.put("/keys/{key_id}")
async def toggle_key(key_id: int, body: KeyToggle, state: State) -> LLMKey:
    row = await state.llm.keys.set_enabled(
        key_id, body.enabled, cap=body.daily_limit, clear=body.clear_daily_limit
    )
    return LLMKey.model_validate(serialise(row))


@router.delete("/keys/{key_id}", status_code=204)
async def remove_key(key_id: int, state: State) -> None:
    await state.llm.keys.remove(key_id)
