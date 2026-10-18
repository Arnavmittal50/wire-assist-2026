from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / "backend" / ".env", extra="ignore")
    gemini_api_key: str = ""
    gemini_analysis_model: str = "gemini-3.8-flash"
    gemini_update_model: str = "gemini-3.5-flash-lite"
    gemini_transcription_model: str = "gemini-3.5-transcribe-live"
    dev_auth_token: str = Field(default="wire-assist-local-dev", min_length=16)
    allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    database_path: Path = ROOT / "backend" / "wire_assist.sqlite3"
    data_dir: Path = ROOT / "data"
    allow_transcript_retention: bool = True
    provider_timeout_seconds: float = 30
    max_segments: int = 300
    max_sessions: int = 100
    max_audio_bytes: int = 64000

    @property
    def origins(self) -> list[str]:
        return [x.strip() for x in self.allowed_origins.split(",") if x.strip()]
