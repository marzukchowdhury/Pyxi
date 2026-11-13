"""Pyxi settings. Same resolution order as Otto: defaults, pyxi.yaml, .env, env vars (PYXI_...)."""

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
    # The lock and sign-in screens, and User Account Control prompts
    r"lockapp",
    r"logonui",
    r"windows default lock screen",
    r"\bconsent\.exe\b",
    r"user account control",
]


class VoiceConfig(BaseModel):
    stt_model: str = Field(
        "base.en", description="faster-whisper model: tiny.en, base.en, small.en..."
    )
    stt_device: Literal["auto", "cpu", "cuda"] = "auto"
    stt_cloud: bool = Field(
        True,
        description="Turn speech into text with the cloud model when it can be reached (far "
        "fewer misheard words); the model on this PC takes over offline. False keeps speech "
        "on this PC. The Voice panel has the same switch.",
    )
    tts: TTSProvider = Field(
        "kokoro", description="kokoro (local CPU), chatterbox (local GPU), client (OS voice)."
    )
    voice: str = Field("af_heart", description="Default Kokoro voice; each agent can pick its own.")
    stt_multilingual_model: str = Field(
        "small",
        description="faster-whisper model used when the language is Automatic or not English.",
    )
    stt_cloud_model: str = Field(
        "google/gemini-3.1-flash-lite",
        description="OpenRouter model that transcribes Bengali (and speech the local model can't "
        "place). Goes through the beta server in the beta. About $0.001 per minute of speech.",
    )
    stt_bengali_model: str = Field(
        "IamSanjid/tugstugi_bengaliai-regional-asr_whisper-medium-ct2",
        description="Offline Bengali listening (1.5 GB, `pyxi setup-voice --bengali`), used when "
        "the cloud can't be reached.",
    )
    speed: float = 1.0
    unload_after_s: float = Field(
        300.0,
        description="Let the speech and voice models go after this many idle seconds (they load "
        "again when the talk key goes down, while you speak). 0 keeps them loaded.",
    )
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
    turn_retention_days: float = Field(
        30.0, description="Forget conversations (and the screenshots they used) older than this."
    )
    max_elements: int = 70
    model_image_width: int = Field(
        1366, description="Screenshots are downscaled to this width for the model."
    )
    uia_budget_s: float = 1.5
    tab_fallback: bool = Field(
        False,
        description="With no browser extension connected, let read_tab and use_tab work in the "
        "browser tab that is showing, through Windows accessibility (pyxi/screenlink.py; what "
        "it can and can't do is in docs/SCREEN-TAB-FALLBACK.md). The extension is always "
        "preferred when one is connected.",
    )
    tab_keys: bool = Field(
        True,
        description="In that mode, may Pyxi press keys in the tab (Enter in a field, Escape, the "
        "arrows)? A key is posted to the browser window that was read, never typed on the "
        "keyboard; Chrome and Edge only hear it while their window is in front.",
    )

    @field_validator("excluded_windows", mode="before")
    @classmethod
    def _split(cls, v: object) -> object:
        return [p.strip() for p in v.split(",") if p.strip()] if isinstance(v, str) else v


class ActionLimits(BaseModel):
    """Outward actions per hour (pyxi/limits.py). At a limit Pyxi stops and asks the user."""

    emails_per_hour: int = Field(10, ge=0)
    posts_per_hour: int = Field(5, ge=0)
    invites_per_hour: int = Field(10, ge=0)
    bookings_per_hour: int = Field(3, ge=0)


class TurnBudget(BaseModel):
    max_tool_rounds: int = Field(6, ge=1, description="Think/act rounds per user turn.")
    max_cost_usd: float = Field(
        0.25, gt=0, description="Per user turn (agent jobs have their own Otto budgets)."
    )
    daily_cost_usd: float = Field(5.0, gt=0, description="Hard stop across all turns in a day.")


class PyxiSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PYXI_",
        env_nested_delimiter="__",
        env_file=".env",
        extra="ignore",
        yaml_file="pyxi.yaml",
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
    library_url: str | None = Field(
        "https://github.com/marzukchowdhury/Pyxi/releases/download/library-v1",
        description="Where real 3D models from Pyxi's library are downloaded from, the first "
        "time each is needed (PYXI_LIBRARY_URL). Empty: only models already on disk.",
    )
    model_3d: str | None = Field(
        "anthropic/claude-sonnet-5.5",
        description="Model that builds 3D models (PYXI_MODEL_3D). A stronger modeller makes far "
        "better shapes (about 15 s and $0.04-0.05 a model with Sonnet 5.5 at low effort); "
        "empty means the chat model builds them itself.",
    )
    effort_3d: Literal["low", "medium", "high", "xhigh", "max"] = "low"
    model_hands: str | None = Field(
        None,
        description="Model that drives the mouse and keyboard (PYXI_MODEL_HANDS); empty = the chat model.",
    )
    model_hands_strong: str | None = Field(
        "anthropic/claude-sonnet-5.5",
        description="Takes over driving when the quick model gets stuck (canvas apps like Google "
        "Sheets, a dialog it can't get past). About 8x the cost per look, only when needed.",
    )
    hands_direct: bool = Field(
        True,
        description="Press buttons, switches, tabs, menu items and list rows through the "
        "accessibility layer, without the mouse, where the control allows it "
        "(PYXI_HANDS_DIRECT). False: every click is a mouse click.",
    )
    model_aim: str | None = Field(
        "anthropic/claude-sonnet-5.5",
        description="Model that finds the parts of a picture Pyxi explains (PYXI_MODEL_AIM), on a "
        "zoomed crop: about $0.004 a picture, run while Pyxi speaks. Empty: the chat model's guess.",
    )

    host: str = "127.0.0.1"
    port: int = 8766
    data_dir: Path = Path(".pyxi")

    voice: VoiceConfig = Field(default_factory=VoiceConfig)
    screen: ScreenConfig = Field(default_factory=ScreenConfig)
    budget: TurnBudget = Field(default_factory=TurnBudget)
    limits: ActionLimits = Field(default_factory=ActionLimits)

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
        return self.data_dir / "pyxi.db"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def crops_dir(self) -> Path:
        return self.data_dir / "crops"

    @property
    def library_dir(self) -> Path:
        return self.data_dir / "library"

    @property
    def palace_dir(self) -> Path:
        return self.data_dir / "palace"

    @property
    def turns_dir(self) -> Path:
        return self.data_dir / "turns"

    @property
    def brain_model(self) -> str:
        return self.local_model if self.local_mode else self.model
