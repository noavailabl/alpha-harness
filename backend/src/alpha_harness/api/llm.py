"""The assistant: keys, models and the prompts it sends.

Prompts are served in full on purpose: one decides what an answer looks like and is
otherwise invisible. Keys are the only secret here, and leave only as a masked hint.
"""

from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..db.models import ChatMessage, Study
from ..labs.params import POWER_POOL_SAMPLER
from ..llm.claude_cli import CLAUDE_MODELS
from ..llm.codex_cli import CODEX_MODELS, LEGACY_MODEL_ID
from ..llm.keys import LLMError, serialise
from ..llm.prompts import PROMPTS
from ..llm.providers import LLMProviders, catalogue
from ..llm.registry import LLMModels, ModelInfo
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
    #: The model's own day, YYYY-MM-DD in its reset time zone.
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
    #: How requests name this model: its provider and id together.
    ref: str
    provider: str
    #: Requests today across every enabled key, each held to its model limit or its cap.
    allowed_today: int
    remaining_today: int
    reset_timezone: str
    #: Until midnight in ``reset_timezone``, when this model's allowance comes back.
    reset_in_seconds: int


class LLMKeyStatus(Out):
    keys: list[LLMKey]
    enabled: int
    budget: list[LLMBudget]
    #: The soonest any set-up model's day turns over, or null with none set up.
    reset_in_seconds: int | None


class KeyWorks(Out):
    key_id: int
    ok: Literal[True]
    #: How many models the key can reach.
    models: int


class KeyFailed(Out):
    key_id: int
    ok: Literal[False]
    error: str


def _check(result: dict[str, Any]) -> KeyWorks | KeyFailed:
    if result["ok"]:
        return KeyWorks.model_validate(result)
    return KeyFailed.model_validate(result)


# --- setup ----------------------------------------------------------------


@router.get("/models")
async def models(state: State) -> LLMModels:
    """The models set up, each with the limits its user gave it."""
    return state.llm.registry.roster()


class SetModel(BaseModel):
    provider: str
    model: str = Field(min_length=1, max_length=200, description="The provider's model id")
    requests_per_minute: int = Field(ge=1)
    requests_per_day: int = Field(ge=1)
    reset_timezone: str = Field(
        min_length=1, max_length=64, description="IANA time zone whose midnight starts a new day"
    )
    max_prompt_tokens: int | None = Field(
        default=None,
        ge=1,
        description="Most tokens one Power Pool prompt may use; null for default",
    )


@router.put("/models")
async def set_model(body: SetModel, state: State) -> ModelInfo:
    """Set a model up with its limits, or change the limits of one already set up."""
    return await state.llm.registry.set(
        body.provider,
        body.model,
        body.requests_per_minute,
        body.requests_per_day,
        body.reset_timezone,
        body.max_prompt_tokens,
    )


@router.delete("/models", status_code=204)
async def remove_model(provider: str, model: str, state: State) -> None:
    """Query parameters, not a path: model ids carry slashes."""
    await state.llm.registry.remove(provider, model)


class OfferedModels(Out):
    #: Model ids the provider lists for this account, sorted. Empty when it would not say.
    models: list[str]
    #: Why the list is empty, when it is. The id can still be typed.
    error: str | None


@router.get("/providers/{provider}/models")
async def offered_models(provider: str, state: State) -> OfferedModels:
    """What a provider's Key can reach, asked live. Costs no generation request."""
    return OfferedModels.model_validate(await state.llm.offered(provider))


class CodexStatus(Out):
    connected: bool
    auth: str | None
    model: str
    reasoning_effort: Literal["medium"]


class CodexRateWindow(Out):
    used_percent: int
    remaining_percent: int
    window_minutes: int | None
    resets_at: int | None


class CodexUsage(Out):
    connected: bool
    plan: str | None
    primary: CodexRateWindow | None
    secondary: CodexRateWindow | None
    has_credits: bool
    credits_balance: str | None
    reset_credits: int
    local_calls: int
    local_tokens: int
    local_tokens_complete: bool
    error: str | None


class ClaudeStatus(Out):
    connected: bool
    installed: bool
    auth: str | None
    plan: str | None
    email: str | None
    model: str
    reasoning_effort: Literal["medium"]


class ClaudeRateWindow(Out):
    used_percent: int
    remaining_percent: int
    resets_at: int | None


class ClaudeUsage(Out):
    connected: bool
    #: "allowed", "allowed_warning" or "rejected", as the CLI last reported it.
    status: str | None
    five_hour: ClaudeRateWindow | None
    seven_day: ClaudeRateWindow | None
    using_overage: bool
    #: Epoch seconds when the snapshot arrived; null until a call has been made.
    observed_at: int | None
    local_calls: int
    local_tokens: int
    local_tokens_complete: bool
    error: str | None


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


def _rate_window(value: Any) -> CodexRateWindow | None:
    if not isinstance(value, dict) or "usedPercent" not in value:
        return None
    used = max(0, min(100, int(value["usedPercent"])))
    return CodexRateWindow(
        used_percent=used,
        remaining_percent=100 - used,
        window_minutes=(
            int(value["windowDurationMins"])
            if value.get("windowDurationMins") is not None
            else None
        ),
        resets_at=int(value["resetsAt"]) if value.get("resetsAt") is not None else None,
    )


async def _local_codex_usage(state: Any) -> tuple[int, int, bool]:
    return await _local_usage(state, {LEGACY_MODEL_ID, *(model.id for model in CODEX_MODELS)})


async def _local_usage(state: Any, model_ids: set[str]) -> tuple[int, int, bool]:
    calls = tokens = 0
    complete = True
    async with state.llm.db.session() as session:
        messages = (
            await session.scalars(select(ChatMessage.meta).where(ChatMessage.role == "assistant"))
        ).all()
        studies = (
            await session.scalars(
                select(Study.sampler_params).where(Study.sampler == POWER_POOL_SAMPLER)
            )
        ).all()
    for meta in messages:
        saved = str(meta.get("model") or "")
        if saved in model_ids or saved.partition(":")[2] in model_ids:
            calls += 1
            tokens += int(meta.get("tokens") or 0)
    for params in studies:
        saved = str(params.get("model") or "")
        if saved not in model_ids and saved.partition(":")[2] not in model_ids:
            continue
        llm = params.get("llm") if isinstance(params.get("llm"), dict) else {}
        history = params.get("calls") if isinstance(params.get("calls"), list) else []
        task_calls = int(llm.get("calls") or 0)
        calls += task_calls
        tokens += sum(int(entry.get("tokens") or 0) for entry in history if isinstance(entry, dict))
        complete = complete and task_calls <= len(history)
    return calls, tokens, complete


@router.get("/codex/usage")
async def codex_usage(state: State) -> CodexUsage:
    """Official shared allowance plus calls recorded locally by Alpha Harness."""
    local_calls, local_tokens, local_complete = await _local_codex_usage(state)
    connected = await state.llm.codex.connected()
    try:
        snapshot = await state.llm.codex.usage()
    except LLMError as exc:
        return CodexUsage(
            connected=connected,
            plan=None,
            primary=None,
            secondary=None,
            has_credits=False,
            credits_balance=None,
            reset_credits=0,
            local_calls=local_calls,
            local_tokens=local_tokens,
            local_tokens_complete=local_complete,
            error=str(exc),
        )
    buckets = snapshot.get("rateLimitsByLimitId")
    candidate = buckets.get("codex") if isinstance(buckets, dict) else None
    fallback = snapshot.get("rateLimits")
    rate: dict[str, Any] = (
        candidate if isinstance(candidate, dict) else fallback if isinstance(fallback, dict) else {}
    )
    raw_credit_status = rate.get("credits")
    credit_status: dict[str, Any] = raw_credit_status if isinstance(raw_credit_status, dict) else {}
    resets = snapshot.get("rateLimitResetCredits")
    return CodexUsage(
        connected=connected,
        plan=str(rate.get("planType")) if rate.get("planType") else None,
        primary=_rate_window(rate.get("primary")),
        secondary=_rate_window(rate.get("secondary")),
        has_credits=bool(credit_status.get("hasCredits")),
        credits_balance=(
            str(credit_status.get("balance")) if credit_status.get("balance") is not None else None
        ),
        reset_credits=(int(resets.get("availableCount") or 0) if isinstance(resets, dict) else 0),
        local_calls=local_calls,
        local_tokens=local_tokens,
        local_tokens_complete=local_complete,
        error=None,
    )


@router.get("/claude")
async def claude_status(state: State) -> ClaudeStatus:
    """Whether the local Claude Code CLI can use the signed-in Claude subscription."""
    from ..llm.claude_cli import DEFAULT_MODEL_ID, MEDIUM_EFFORT

    claude = state.llm.claude
    connected = await claude.connected()
    status = claude.status
    plan = status.get("subscriptionType") or status.get("subscription")
    return ClaudeStatus(
        connected=connected,
        installed=claude.executable() is not None,
        auth="Claude subscription" if connected else None,
        plan=str(plan) if plan else None,
        email=str(status["email"]) if status.get("email") else None,
        model=DEFAULT_MODEL_ID,
        reasoning_effort=MEDIUM_EFFORT,
    )


def _claude_window(value: Any) -> ClaudeRateWindow | None:
    if not isinstance(value, dict) or value.get("utilization") is None:
        return None
    raw = float(value["utilization"])
    # The CLI reports a fraction; tolerate a percentage too.
    used = max(0, min(100, round(raw * 100 if raw <= 1 else raw)))
    return ClaudeRateWindow(
        used_percent=used,
        remaining_percent=100 - used,
        resets_at=int(value["resetsAt"]) if value.get("resetsAt") is not None else None,
    )


@router.get("/claude/usage")
async def claude_usage(state: State, refresh: bool = False) -> ClaudeUsage:
    """The plan's allowance as the last Claude call reported it, plus local records.

    ``refresh`` spends one tiny Haiku request to read it now.
    """
    claude = state.llm.claude
    local_calls, local_tokens, local_complete = await _local_usage(
        state, {model.id for model in CLAUDE_MODELS}
    )
    connected = await claude.connected()
    error = None
    if refresh or (connected and claude.limits is None and not claude.probed):
        try:
            await claude.probe()
        except LLMError as exc:
            error = str(exc)
    limits: dict[str, Any] = claude.limits or {}
    raw_windows = limits.get("unifiedWindows")
    windows: dict[str, Any] = raw_windows if isinstance(raw_windows, dict) else {}
    five = windows.get("five_hour")
    seven = windows.get("seven_day")
    # Older CLIs report only the window that is binding right now.
    if five is None and seven is None and limits.get("utilization") is not None:
        single = {"utilization": limits["utilization"], "resetsAt": limits.get("resetsAt")}
        if limits.get("rateLimitType") == "seven_day":
            seven = single
        else:
            five = single
    return ClaudeUsage(
        connected=connected,
        status=str(limits["status"]) if limits.get("status") else None,
        five_hour=_claude_window(five),
        seven_day=_claude_window(seven),
        using_overage=bool(limits.get("isUsingOverage")),
        observed_at=int(claude.limits_at) if claude.limits_at else None,
        local_calls=local_calls,
        local_tokens=local_tokens,
        local_tokens_complete=local_complete,
        error=error,
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
