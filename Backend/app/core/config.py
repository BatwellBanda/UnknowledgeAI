from pathlib import Path
from typing import Literal
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    DATABASE_URL: str = Field(repr=False)
    SECRET_KEY: str = Field(repr=False)
    LLM_PROVIDER: Literal["disabled", "ollama"] = "ollama"
    OLLAMA_BASE_URL: str = "http://127.0.0.1:11434"
    OLLAMA_MODEL: str = Field(default="qwen2.5:1.5b", min_length=1, max_length=100)
    LLM_TIMEOUT_SECONDS: float = Field(default=120, ge=1, le=600)
    LLM_MAX_OUTPUT_TOKENS: int = Field(default=768, ge=128, le=2048)

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
    )

settings = Settings()
