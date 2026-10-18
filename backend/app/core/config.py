from pathlib import Path
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / "backend" / ".env", extra="ignore")
    gemini_api_key: str = ""
    twilio_account_sid: str = ""
    twilio_api_key_sid: str = ""
    twilio_api_key_secret: str = Field(default="", repr=False)
    twilio_auth_token: str = Field(default="", repr=False)
    twilio_twiml_app_sid: str = ""
    twilio_phone_number: str = ""
    test_member_phone_number: str = ""
    public_backend_url: str = ""
    groq_api_key: str = Field(default="", repr=False)
    groq_model: str = "openai/gpt-oss-20b"
    groq_precall_model: str = "openai/gpt-oss-120b"
    groq_min_interval_seconds: float = Field(default=8, ge=1, le=120)
    groq_debounce_seconds: float = Field(default=0.7, ge=0, le=5)
    twilio_audio_queue_frames: int = Field(default=250, ge=10, le=500)
    gemini_analysis_model: str = "gemini-3.5-flash-lite"
    gemini_update_model: str = "gemini-3.1-flash-lite"
    gemini_transcription_model: str = "gemini-3.5-transcribe-live"
    gemini_diarization_model: str = "gemini-3.5-transcribe"
    batch_duration_seconds: float = Field(default=12, ge=2, le=30)
    batch_overlap_seconds: float = Field(default=2, ge=0, le=10)
    max_pending_batches: int = Field(default=3, ge=1, le=5)
    diarization_word_timestamps: bool = True
    guidance_timeout_seconds: float = Field(default=12, ge=1, le=30)
    guidance_review_pass: bool = False
    dev_auth_token: str = Field(default="wire-assist-local-dev", min_length=16)
    allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    database_path: Path = ROOT / "backend" / "wire_assist.sqlite3"
    data_dir: Path = ROOT / "data"
    allow_transcript_retention: bool = True
    provider_timeout_seconds: float = 30
    provider_retry_cooldown_seconds: float = Field(default=60, ge=1)
    max_segments: int = 300
    max_sessions: int = 100
    max_audio_bytes: int = 64000

    @model_validator(mode="after")
    def valid_batch_overlap(self):
        if self.batch_duration_seconds - self.batch_overlap_seconds < 1:
            raise ValueError("Batch duration must exceed overlap by at least one second")
        return self

    @property
    def origins(self) -> list[str]:
        return [x.strip() for x in self.allowed_origins.split(",") if x.strip()]
