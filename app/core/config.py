"""
Runtime configuration.

One `Settings` object describes every environment-driven knob in the stack.
It is resolved through `get_settings()` (LRU-cached) and injected via FastAPI's
dependency system — never imported as a module-level singleton, so tests can
override it and importing any module does not require a live API key.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Voices the Bulbul v3 TTS model accepts, as the API itself reports them in
# its 400 body. Validated at boot because the failure mode otherwise is
# silent: an invalid speaker returns HTTP 400 on *every* synthesis call, the
# pipeline degrades each turn to text, and a voice product ships with no
# voice at all. Update this set if Sarvam adds voices to the model.
BULBUL_V3_SPEAKERS: frozenset[str] = frozenset(
    {
        "aditya",
        "ritu",
        "ashutosh",
        "priya",
        "neha",
        "rahul",
        "pooja",
        "rohan",
        "simran",
        "kavya",
        "amit",
        "dev",
        "ishita",
        "shreya",
        "ratan",
        "varun",
        "manan",
        "sumit",
        "roopa",
        "kabir",
        "aayan",
        "shubh",
        "advait",
        "anand",
        "tanya",
        "tarun",
        "sunny",
        "mani",
        "gokul",
        "vijay",
        "shruti",
        "suhani",
        "mohit",
        "kavitha",
        "rehan",
        "soham",
        "rupali",
    }
)


class Settings(BaseSettings):
    """Strongly-typed runtime configuration for the IVR engine."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # -- Secrets ---------------------------------------------------------------
    sarvam_api_key: str = Field(
        ...,
        description="Sarvam AI subscription key. Backs STT, chat completions, and TTS alike.",
    )
    operator_api_keys: str = Field(
        default="",
        description=(
            "Comma-separated API keys authorized for operator endpoints (CRM reads/writes, "
            "telemetry). Empty disables those endpoints entirely rather than exposing them."
        ),
    )

    # -- Text-generation brain --------------------------------------------------
    sarvam_text_model: str = Field(default="sarvam-105b-conversations")
    sarvam_text_max_tokens: int = Field(default=220, gt=0)
    sarvam_text_temperature: float = Field(default=0.4, ge=0.0, le=2.0)
    sarvam_text_timeout_seconds: float = Field(default=15.0, gt=0)
    reply_max_words: int = Field(
        default=30,
        gt=0,
        description="Spoken-length target the persona prompts aim for.",
    )
    reply_hard_max_words: int = Field(
        default=50,
        gt=0,
        description=(
            "Ceiling enforced after generation. Above it, whole trailing sentences are "
            "dropped; a sentence is never cut open."
        ),
    )
    max_history_turns: int = Field(default=12, gt=0)

    # -- Voice pipeline ---------------------------------------------------------
    stt_model: str = Field(default="saaras:v3")
    stt_language_code: str = Field(
        default="unknown",
        description="BCP-47 language hint for STT; 'unknown' enables auto-detection.",
    )
    tts_model: str = Field(default="bulbul:v3")
    tts_speaker: str = Field(
        default="priya",
        description="Bulbul voice identity. Validated against the model — see BULBUL_V3_SPEAKERS.",
    )
    tts_target_language_code: str = Field(
        default="en-IN",
        description="Fallback BCP-47 target for TTS when STT cannot determine the language.",
    )
    tts_follow_detected_language: bool = Field(
        default=True,
        description=(
            "Synthesize the reply in the language STT detected, instead of always using "
            "tts_target_language_code. Keeps a Hindi caller's reply spoken in Hindi."
        ),
    )
    tts_output_audio_codec: str = Field(default="wav")
    tts_output_sample_rate: int = Field(default=22050, gt=0)
    voice_api_timeout_seconds: float = Field(default=15.0, gt=0)

    # -- Resilience -------------------------------------------------------------
    sarvam_max_retries: int = Field(
        default=2,
        ge=0,
        description="Retries for transient Sarvam failures (429 / 5xx / timeouts).",
    )
    sarvam_retry_base_delay_seconds: float = Field(default=0.4, gt=0)

    # -- Limits -----------------------------------------------------------------
    max_audio_upload_bytes: int = Field(
        default=10 * 1024 * 1024,
        gt=0,
        description="Hard ceiling on a single uploaded audio turn (bytes).",
    )
    rate_limit_turns_per_minute: int = Field(
        default=20,
        ge=0,
        description="Per-caller ceiling on pipeline turns per minute. 0 disables limiting.",
    )

    # -- State backend ----------------------------------------------------------
    redis_url: str = Field(
        default="",
        description=(
            "Redis connection URL for session / telemetry / CRM state. When empty the "
            "engine falls back to per-process in-memory state and REFUSES to run "
            "multiple workers (see app.main)."
        ),
    )
    session_ttl_seconds: int = Field(default=1800, gt=0)
    telemetry_history_size: int = Field(default=200, gt=0)

    # -- CRM --------------------------------------------------------------------
    crm_default_country_code: str = Field(default="+91")

    # -- Server -----------------------------------------------------------------
    app_env: Literal["development", "staging", "production"] = Field(default="production")
    log_level: str = Field(default="INFO")
    log_format: Literal["json", "console"] = Field(default="json")
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000)
    cors_allow_origins: str = Field(
        default="",
        description=(
            "Comma-separated origins allowed to call the API from a browser (the Next.js "
            "frontend). Empty means same-origin only. '*' is rejected in production."
        ),
    )

    # -- Derived views ----------------------------------------------------------
    @property
    def operator_api_key_set(self) -> frozenset[str]:
        return frozenset(k.strip() for k in self.operator_api_keys.split(",") if k.strip())

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

    @property
    def uses_redis(self) -> bool:
        return bool(self.redis_url.strip())

    @field_validator("sarvam_api_key")
    @classmethod
    def _reject_placeholder_secret(cls, value: str) -> str:
        """Fail at boot rather than on the first live call."""
        stripped = (value or "").strip()
        if not stripped or stripped.lower().startswith(("your_", "changeme", "placeholder")):
            raise ValueError(
                "SARVAM_API_KEY is not set. Populate it in .env or the environment "
                "before starting the service."
            )
        return stripped

    @model_validator(mode="after")
    def _speaker_must_suit_the_model(self) -> Settings:
        """Reject a speaker the configured TTS model will not accept.

        Failing the boot is the whole point: the alternative is an HTTP 400 on
        every single synthesis call, which the pipeline dutifully degrades to
        text — so the service looks healthy while silently never speaking.
        """
        if self.tts_model.startswith("bulbul:v3") and self.tts_speaker not in BULBUL_V3_SPEAKERS:
            raise ValueError(
                f"TTS_SPEAKER={self.tts_speaker!r} is not a valid voice for "
                f"{self.tts_model!r}. Valid voices: {', '.join(sorted(BULBUL_V3_SPEAKERS))}."
            )
        return self

    @field_validator("cors_allow_origins")
    @classmethod
    def _reject_wildcard_cors(cls, value: str, info: object) -> str:
        # A wildcard here would expose operator endpoints to any origin that
        # obtained a key. Explicit origins only.
        if "*" in value:
            raise ValueError("CORS_ALLOW_ORIGINS must list explicit origins; '*' is not accepted.")
        return value


@lru_cache
def get_settings() -> Settings:
    """Process-wide cached settings. Override in tests via `get_settings.cache_clear()`."""
    return Settings()
