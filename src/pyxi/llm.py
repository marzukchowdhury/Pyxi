"""Model access, reusing Otto's provider layer (Anthropic, OpenAI, OpenAI-compatible/Ollama)."""

from __future__ import annotations

import os

from otto.config import OttoSettings
from otto.llm import make_provider
from otto.llm.base import LLMProvider

from pyxi.config import PyxiSettings


def otto_settings(
    settings: PyxiSettings, *, model: str | None = None, **overrides: object
) -> OttoSettings:
    """Build Otto settings that mirror Pyxi's model choice (cloud or local)."""
    base: dict[str, object] = {
        "effort": settings.effort,
        "data_dir": settings.data_dir / "otto",
        # Scout's browser: a profile of its own in Pyxi's data folder, never the user's Chrome or
        # Edge profile, cookies or saved passwords, and never attached to a running browser.
        "profile_dir": settings.data_dir / "browser",
        "cdp_url": None,
    }
    if settings.local_mode:
        base.update(
            model=model or settings.local_model,
            provider="openai_compatible",
            base_url=settings.local_base_url,
        )
    else:
        base.update(model=model or settings.model)
        if beta := os.environ.get(
            "PYXI_OPENROUTER_URL"
        ):  # the beta server stands in for OpenRouter
            base.update(provider="openrouter", base_url=beta)
    base.update(overrides)
    return OttoSettings(**base)


def make_llm(settings: PyxiSettings, *, model: str | None = None) -> LLMProvider:
    return make_provider(otto_settings(settings, model=model))
