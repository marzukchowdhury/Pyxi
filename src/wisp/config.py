"""Wisp settings. Same resolution order as Otto: defaults, wisp.yaml, .env, env vars (WISP_...)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import (
    BaseSettings,
    NoDecode,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

TTSProvider = Literal["kokoro", "chatterbox", "client", "none"]

DEFAULT_EXCLUDED = [
    r"1password",
    r"bitwarden",
    r"keepass",
    r"lastpass",
    r"dashlane",
    r"\bbank\b",
    r"banking",
    r"credential",
    r"windows security",
]


class VoiceConfig(BaseModel):
    stt_model: str = Field(
        "base.en", description="faster-whisper model: tiny.en, base.en, small.en..."
    )
    stt_device: Literal["auto", "cpu", "cuda"] = "auto"
    tts: TTSProvider = Field(
        "kokoro", description="kokoro (local CPU), chatterbox (local GPU), client (OS voice)."
    )
    voice: str = Field("af_heart", description="Default Kokoro voice; each agent can pick its own.")
    stt_multilingual_model: str = Field(
        "small",
        description="faster-whisper model used when the language is Automatic or not English.",
    )
    speed: float = 1.0
    chatterbox_reference: Path | None = Field(
        None, description="Reference clip for Chatterbox voice cloning."
    )


class ScreenConfig(BaseModel):
    excluded_windows: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: list(DEFAULT_EXCLUDED),
        description="Regexes on window title / process name. Matching windows are never captured.",
    )
    ocr: bool = Field(
        True,
        description="Read text from the pixels too (Windows OCR, offline), so text inside images can be framed.",
    )
    max_text_lines: int = Field(90, description="Most OCR lines given to the model per look.")
    ambient_memory: bool = Field(
        False,
        description="Build the Memory Palace (opt-in): window, focus and UI metadata over time.",
    )
    focus_poll_s: float = Field(
        1.0, description="How often the focused element is checked (cheap)."
    )
    snapshot_interval_s: float = Field(
        30.0, description="Full UI snapshot at least this often (and on every window switch)."
    )
    keep_screenshots: bool = Field(
        False, description="Also keep downscaled screenshots, so time travel shows real pixels."
    )
    retention_hours: float = Field(72.0, description="Forget palace history older than this.")
    max_elements: int = 70
    model_image_width: int = Field(
        1366, description="Screenshots are downscaled to this width for the model."
    )
    uia_budget_s: float = 1.5

    @field_validator("excluded_windows", mode="before")
    @classmethod
    def _split(cls, v: object) -> object:
        return [p.strip() for p in v.split(",") if p.strip()] if isinstance(v, str) else v


class TurnBudget(BaseModel):
    max_tool_rounds: int = Field(6, ge=1, description="Think/act rounds per user turn.")
    max_cost_usd: float = Field(
        0.25, gt=0, description="Per user turn (agent jobs have their own Otto budgets)."
    )
    daily_cost_usd: float = Field(5.0, gt=0, description="Hard stop across all turns in a day.")


class WispSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="WISP_",
        env_nested_delimiter="__",
        env_file=".env",
        extra="ignore",
        yaml_file="wisp.yaml",
    )

    model: str = "claude-opus-5-5"
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "low"
    local_mode: bool = Field(
        False, description="Use a local Ollama vision model; nothing leaves the machine."
    )
    local_model: str = "qwen2.5vl:7b"
    local_base_url: str = "http://localhost:11434/v1"
    job_model: str | None = Field(
        None, description="Model for agent jobs (defaults to the main model)."
    )
    model_3d: str | None = Field(
        "anthropic/claude-sonnet-5.5",
        description="Model that builds 3D models (WISP_MODEL_3D). A stronger modeller makes far "
        "better shapes (about 15 s and $0.04-0.05 a model with Sonnet 5.5 at low effort); "
        "empty means the chat model builds them itself.",
    )
    effort_3d: Literal["low", "medium", "high", "xhigh", "max"] = "low"
    model_aim: str | None = Field(
        "anthropic/claude-sonnet-5.5",
        description="Model that finds the parts of a picture Wisp explains (WISP_MODEL_AIM), on a "
        "zoomed crop: about $0.004 a picture, run while Wisp speaks. Empty: the chat model's guess.",
    )

    host: str = "127.0.0.1"
    port: int = 8766
    data_dir: Path = Path(".wisp")

    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    screen: ScreenConfig = Field(default_factory=ScreenConfig)
    budget: TurnBudget = Field(default_factory=TurnBudget)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )

    @property
    def db_path(self) -> Path:
        return self.data_dir / "wisp.db"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def crops_dir(self) -> Path:
        return self.data_dir / "crops"

    @property
    def palace_dir(self) -> Path:
        return self.data_dir / "palace"

    @property
    def turns_dir(self) -> Path:
        return self.data_dir / "turns"

    @property
    def brain_model(self) -> str:
        return self.local_model if self.local_mode else self.model
