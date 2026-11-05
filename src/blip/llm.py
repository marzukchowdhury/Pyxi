"""Model access, reusing Otto's provider layer (Anthropic, OpenAI, OpenAI-compatible/Ollama)."""

from __future__ import annotations

from otto.config import OttoSettings
from otto.llm import make_provider
from otto.llm.base import LLMProvider

from blip.config import BlipSettings


def otto_settings(
    settings: BlipSettings, *, model: str | None = None, **overrides: object
) -> OttoSettings:
    """Build Otto settings that mirror Blip's model choice (cloud or local)."""
    base: dict[str, object] = {"effort": settings.effort, "data_dir": settings.data_dir / "otto"}
    if settings.local_mode:
        base.update(
            model=model or settings.local_model,
            provider="openai_compatible",
            base_url=settings.local_base_url,
        )
    else:
        base.update(model=model or settings.model)
    base.update(overrides)
    return OttoSettings(**base)


def make_llm(settings: BlipSettings, *, model: str | None = None) -> LLMProvider:
    return make_provider(otto_settings(settings, model=model))
