"""Which models exist, and what each one costs you.

Three limits apply at once on the AI Studio free tier, and they are not equally
important:

* **RPM** — requests per minute. Recovers in sixty seconds; briefly annoying.
* **TPM** — tokens per minute. Also recovers in sixty seconds.
* **RPD** — requests per day. **This is the one that ends your session.** It does not
  recover until midnight Pacific, and it varies by a factor of twenty-five across the
  roster: twenty a day on Gemini 3.8 Flash, five hundred on 3.5 Flash Lite.

That last point drives the design: every model carries its daily budget where it is chosen,
the Lite models are marked for bulk work, and someone who picks the newest model because it
sounds best does not get twenty questions and then silence.

**Nothing here is authoritative except as a starting point.** Google publishes no endpoint
for free-tier quotas, so the numbers below are transcribed and *will* drift; correcting one
is a one-line edit here.
"""

from __future__ import annotations

from typing import Literal

import structlog
from pydantic.dataclasses import dataclass

from ..schemas import WIRE, Out

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True, config=WIRE)
class ModelInfo:
    """One model and its free-tier budget, as the screens show it."""

    id: str
    label: str
    #: "text" for generation, "embedding", or "open" for the Gemma family.
    kind: Literal["text", "embedding", "open"]
    rpm: int
    tpm: int
    rpd: int
    #: True for a model with a daily budget large enough to work in.
    bulk: bool = False
    recommended: bool = False
    #: Set when the model came from the API rather than the built-in table, meaning its
    #: limits are guesses rather than transcribed.
    discovered: bool = False
    #: Whose key answers for this model. A key only ever serves its own provider.
    provider: str = "google"


class ModelDefaults(Out):
    chat: str
    deep: str


class LLMModels(Out):
    models: list[ModelInfo]
    defaults: ModelDefaults
    note: str


#: Transcribed from the AI Studio free-tier rate limits. Expect to correct these.
BUILTIN: tuple[ModelInfo, ...] = (
    ModelInfo(
        "gemini-3.8-flash",
        "Gemini 3.8 Flash",
        "text",
        rpm=5,
        tpm=250_000,
        rpd=20,
        recommended=True,
    ),
    ModelInfo(
        "gemini-3.7-flash",
        "Gemini 3.7 Flash",
        "text",
        rpm=5,
        tpm=250_000,
        rpd=20,
    ),
    ModelInfo(
        "gemini-3.6-flash",
        "Gemini 3.6 Flash",
        "text",
        rpm=5,
        tpm=250_000,
        rpd=20,
    ),
    ModelInfo(
        "gemini-3.5-flash",
        "Gemini 3.5 Flash",
        "text",
        rpm=5,
        tpm=250_000,
        rpd=20,
    ),
    ModelInfo(
        "gemini-3.5-flash-lite",
        "Gemini 3.5 Flash Lite",
        "text",
        rpm=15,
        tpm=250_000,
        rpd=500,
        bulk=True,
        recommended=True,
    ),
    ModelInfo(
        "gemini-3.1-flash-lite",
        "Gemini 3.1 Flash Lite",
        "text",
        rpm=15,
        tpm=250_000,
        rpd=500,
        bulk=True,
    ),
    ModelInfo(
        "gemma-4-31b-it",
        "Gemma 4 31B",
        "open",
        rpm=30,
        tpm=16_000,
        rpd=14_400,
        bulk=True,
    ),
    ModelInfo(
        "gemma-4-26b-a4b-it",
        "Gemma 4 26B",
        "open",
        rpm=30,
        tpm=16_000,
        rpd=14_400,
        bulk=True,
    ),
)

#: For a model discovered from the API with no published limits. The *most* restrictive real
#: row, so an unknown model cannot silently burn a day's quota.
UNKNOWN_LIMITS = {"rpm": 5, "tpm": 250_000, "rpd": 20}

DEFAULT_MODEL = "gemini-3.5-flash-lite"
#: For a single hard question where quality matters more than the daily budget.
DEEP_MODEL = "gemini-3.8-flash"


class ModelRegistry:
    """The model roster: the table above, plus whatever the API turns out to offer."""

    def __init__(self) -> None:
        self._models: dict[str, ModelInfo] = {m.id: m for m in BUILTIN}
        # Imported late: providers describes itself in terms of ModelInfo, so importing
        # it at module level would be a cycle.
        from .providers import provider_models

        for model in provider_models():
            self._models.setdefault(model.id, model)

        from .codex_cli import CODEX_MODELS

        for model in CODEX_MODELS:
            self._models.setdefault(
                model.id,
                ModelInfo(
                    id=model.id,
                    label=model.label,
                    kind="text",
                    provider="codex",
                    rpm=60,
                    tpm=1_000_000,
                    rpd=1_000_000,
                    bulk=True,
                ),
            )

    # -- reading ---------------------------------------------------------

    def get(self, model_id: str) -> ModelInfo | None:
        return self._models.get(model_id)

    def all(self, kind: str | None = None) -> list[ModelInfo]:
        """Every model, richest daily budget first — because that is what runs out."""
        models = [m for m in self._models.values() if kind is None or m.kind == kind]
        return sorted(models, key=lambda m: (-m.rpd, -m.rpm, m.id))

    def roster(self) -> LLMModels:
        return LLMModels(
            models=self.all(),
            defaults=ModelDefaults(chat=DEFAULT_MODEL, deep=DEEP_MODEL),
            note=(
                "Requests per day is the limit that ends a session — it does not reset "
                "until midnight Pacific, and it varies twenty-five-fold across these "
                "models. Adding a second API key doubles it."
            ),
        )

    # -- editing ---------------------------------------------------------

    def merge_discovered(self, names: list[str], provider: str) -> list[str]:
        """Add models the API reports that we have never heard of, for a provider with no
        roster here: OpenAI's moves too fast to transcribe.

        A provider with a transcribed roster keeps it. Its model list also holds speech,
        image and video models, and ones retired for new keys, none of which can answer.
        Discovered limits are unknown, so they get the most restrictive real budget and are
        flagged ``discovered`` rather than passing a guess off as a measurement.
        """
        if any(m.provider == provider and not m.discovered for m in self._models.values()):
            return []
        added: list[str] = []
        for raw in names:
            model_id = raw.removeprefix("models/")
            if model_id in self._models:
                continue
            kind = (
                "embedding"
                if "embedding" in model_id
                else "open"
                if "gemma" in model_id
                else "text"
            )
            self._models[model_id] = ModelInfo(
                id=model_id,
                label=model_id.replace("-", " ").title(),
                kind=kind,
                discovered=True,
                provider=provider,
                rpm=UNKNOWN_LIMITS["rpm"],
                tpm=UNKNOWN_LIMITS["tpm"],
                rpd=UNKNOWN_LIMITS["rpd"],
            )
            added.append(model_id)
        if added:
            log.info("llm.registry.discovered", models=added)
        return added
