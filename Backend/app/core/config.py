from pathlib import Path
from typing import Literal
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    DATABASE_URL: str = Field(repr=False)
    # HS256 needs a long random key; generate one as described in the README.
    SECRET_KEY: str = Field(repr=False, min_length=32)
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=60, ge=5, le=24 * 60)
    LLM_PROVIDER: Literal["disabled", "ollama"] = "ollama"
    OLLAMA_BASE_URL: str = "http://127.0.0.1:11434"
    OLLAMA_MODEL: str = Field(default="qwen2.5:1.5b", min_length=1, max_length=100)
    LLM_TIMEOUT_SECONDS: float = Field(default=120, ge=1, le=600)
    LLM_MAX_OUTPUT_TOKENS: int = Field(default=768, ge=128, le=2048)

    # Storage is anchored to Backend/, independent of the process working directory.
    UPLOAD_DIR: Path = BACKEND_DIR / "uploads"
    MAX_USER_STORAGE_MB: int = Field(default=500, ge=1)

    # Untrusted documents are parsed in a child process that is killed on timeout; 0 disables isolation.
    EXTRACTION_TIMEOUT_SECONDS: float = Field(default=120, ge=0, le=3600)

    # Bound slow model work so it cannot occupy every request thread.
    MAX_CONCURRENT_GENERATIONS: int = Field(default=2, ge=1, le=64)
    MAX_CONCURRENT_EMBEDDINGS: int = Field(default=4, ge=1, le=64)
    BUSY_WAIT_SECONDS: float = Field(default=10, ge=0, le=120)

    # Lecture recordings: FFmpeg checks and decodes them; Whisper (via transformers) transcribes them.
    # With transcription disabled, a caption file (.vtt or .srt) supplies the timestamped transcript.
    MAX_RECORDING_MB: int = Field(default=200, ge=1, le=4096)
    MAX_RECORDING_MINUTES: int = Field(default=180, ge=1, le=24 * 60)
    FFMPEG_PATH: str = "ffmpeg"
    FFPROBE_PATH: str = "ffprobe"
    TRANSCRIPTION_PROVIDER: Literal["disabled", "whisper"] = "whisper"
    WHISPER_MODEL: str = Field(default="openai/whisper-base", min_length=1, max_length=200)
    # A Whisper language name such as "english"; empty lets the model detect the language.
    TRANSCRIPTION_LANGUAGE: str = Field(default="english", max_length=40)
    MEDIA_LINK_MINUTES: int = Field(default=120, ge=5, le=24 * 60)

    RATE_LIMIT_ENABLED: bool = True
    # Only enable when a trusted reverse proxy sets X-Forwarded-For; otherwise clients can spoof it.
    TRUST_PROXY_HEADERS: bool = False

    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env")


settings = Settings()
