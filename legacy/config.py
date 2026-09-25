"""
config.py
==============================================================================
NovaConnect Telecoms Conversational IVR — Runtime Configuration Layer
------------------------------------------------------------------------------
Centralizes and validates every environment-driven setting used across the
platform (agent brain, voice pipeline, CRM data plane, and the FastAPI/
Streamlit surfaces). Built on `pydantic-settings` so that:

  * Values are cast to the correct type at process start (fail-fast).
  * Missing required secrets raise a clear, actionable error instead of a
    downstream `KeyError`/`None` propagating into a live call.
  * A single `Settings` object is cached (`lru_cache`) and shared across the
    whole process, avoiding repeated `.env` parsing on every request.

The entire conversational stack — speech-to-text, the text-generation brain,
and text-to-speech — now routes exclusively through Sarvam AI. There is no
third-party LLM dependency: `SARVAM_API_KEY` is the only secret this service
requires.
==============================================================================
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Strongly-typed runtime configuration for the entire IVR stack.

    All fields can be overridden via environment variables or a local
    `.env` file (see `.env` in the project root). Field names are matched
    case-insensitively against environment variable names.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # -- Required secrets -----------------------------------------------------
    # Single credential for the whole platform: Sarvam AI backs STT, the text
    # generation brain, and TTS alike.
    sarvam_api_key: str = Field(
        ...,
        description="Subscription key for the Sarvam AI platform (STT, text generation, and TTS).",
    )

    # -- Sarvam AI text-generation brain configuration -------------------------
    # `client.chat.completions(...)` model id. "sarvam-105b-conversations" is
    # Sarvam's conversational-tuned chat model; swap via SARVAM_TEXT_MODEL for
    # any other model the account's Sarvam credits unlock (e.g. "sarvam-105b").
    sarvam_text_model: str = Field(
        default="sarvam-105b-conversations",
        description="Sarvam AI chat-completions model id used for intent triage and dialogue generation.",
    )
    sarvam_text_max_tokens: int = Field(
        default=120,
        description="Hard ceiling on generated tokens — keeps vocal replies short and fast.",
    )
    sarvam_text_temperature: float = Field(
        default=0.4,
        description="Sampling temperature for the text-generation brain; kept low for disciplined, consistent replies.",
    )
    sarvam_text_timeout_seconds: float = Field(
        default=15.0,
        description="Per-request timeout applied to Sarvam AI chat-completions calls.",
    )
    reply_max_words: int = Field(
        default=15,
        description="Enterprise discipline ceiling: spoken replies must stay under this word count.",
    )
    max_history_turns: int = Field(
        default=12,
        description="Number of conversational turns retained per session before truncation.",
    )

    # -- Sarvam AI voice pipeline configuration -------------------------------
    # Model id per the installed `sarvamai` SDK's speech_to_text.transcribe
    # spec (Saaras family; the unified STT endpoint's `mode` param, set to
    # "transcribe" in voice_pipeline.py, selects same-language transcription
    # over translation).
    stt_model: str = Field(
        default="saaras:v3",
        description="Sarvam Speech-to-Text model spec (Saaras family).",
    )
    stt_language_code: str = Field(
        default="unknown",
        description="BCP-47 language hint for STT; 'unknown' enables auto language detection.",
    )
    tts_model: str = Field(
        default="bulbul:v3",
        description="Sarvam Text-to-Speech model spec (Bulbul family).",
    )
    tts_speaker: str = Field(
        default="priya",
        description="Default Bulbul voice/speaker identity used for synthesized replies.",
    )
    tts_target_language_code: str = Field(
        default="en-IN",
        description="BCP-47 target language for synthesized speech output.",
    )
    tts_output_audio_codec: str = Field(
        default="wav",
        description="Container/codec requested from Sarvam TTS; must match the media_type main.py streams back.",
    )
    tts_output_sample_rate: int = Field(
        default=22050,
        description="Output sample rate (Hz) requested from Sarvam TTS.",
    )
    voice_api_timeout_seconds: float = Field(
        default=15.0,
        description="Per-request timeout applied to all Sarvam AI STT/TTS calls.",
    )

    # -- CRM / BSS data plane --------------------------------------------------
    crm_default_country_code: str = Field(
        default="+234",
        description="Default MSISDN country-code prefix for the simulated telecom subscriber base.",
    )

    # -- Application/server behaviour ------------------------------------------
    app_env: str = Field(default="production", description="Deployment environment label.")
    log_level: str = Field(default="INFO", description="Root logging level.")
    host: str = Field(default="0.0.0.0", description="Bind host for the FastAPI/uvicorn server.")
    port: int = Field(default=8000, description="Bind port for the FastAPI/uvicorn server.")
    api_base_url: str = Field(
        default="http://localhost:8000",
        description="Base URL the Streamlit dashboard uses to reach the FastAPI engine.",
    )
    telemetry_history_size: int = Field(
        default=200,
        description="Maximum number of past call-telemetry records retained in memory for the dashboard.",
    )

    @field_validator("sarvam_api_key")
    @classmethod
    def _reject_placeholder_secrets(cls, value: str, info) -> str:
        """
        Fail fast if the Sarvam API key is empty or still holds an obvious
        placeholder value (e.g. left over from `.env` scaffolding), rather
        than allowing the process to boot and fail confusingly on the first
        live API call.
        """
        stripped = (value or "").strip()
        if not stripped or stripped.lower().startswith("your_"):
            raise ValueError(
                f"{info.field_name} is not set. Populate it in your .env file "
                f"or environment before starting the service."
            )
        return stripped

    @field_validator("reply_max_words")
    @classmethod
    def _reply_word_cap_must_be_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("reply_max_words must be a positive integer.")
        return value


@lru_cache
def get_settings() -> Settings:
    """
    Returns a process-wide cached Settings instance.

    Using `lru_cache` guarantees `.env` is parsed and validated exactly once
    per process, and every module (agent_brain, voice_pipeline, crm_service,
    main, dashboard) observes the same configuration snapshot.
    """
    return Settings()


# Module-level convenience singleton for straightforward `from config import settings`
# imports across the codebase.
settings = get_settings()
