from __future__ import annotations

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", Path(".env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Set to false once OPENROUTER_API_KEY is configured
    cockpit_mock_mode: bool = True
    cockpit_host: str = "127.0.0.1"
    cockpit_port: int = 8000
    cockpit_db_path: str = str(REPO_ROOT / "backend" / "data" / "cockpit.db")
    cockpit_cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # OpenRouter (STT + Jev System-1 router + System-2 synthesis)
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api"
    openrouter_jev_model: str = "~typesafe/jev-latest"
    openrouter_stt_model: str = "openai/whisper-1"
    openrouter_llm_model: str = "anthropic/claude-haiku-4.5"

    # Notion (optional live sync for Track 4)
    notion_token: str = ""
    notion_parent_page_id: str = ""
    notion_log_database_id: str = ""
    notion_holdings_database_id: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cockpit_cors_origins.split(",") if origin.strip()]

    @property
    def live_openrouter_enabled(self) -> bool:
        return (not self.cockpit_mock_mode) and bool(self.openrouter_api_key.strip())

    @property
    def notion_enabled(self) -> bool:
        return bool(self.notion_token.strip() and self.notion_log_database_id.strip())
