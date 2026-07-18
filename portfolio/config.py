"""Application configuration loaded from the project's .env file."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
load_dotenv(ENV_PATH, override=False)


@dataclass(frozen=True)
class Settings:
    project_root: Path
    database_path: Path
    groq_api_key: str | None
    groq_model: str
    lseg_session_name: str
    lseg_app_key: str | None = None
    lseg_session_timeout: float = 8.0
    lseg_request_timeout: float = 20.0


def get_settings(database_path: Path | None = None) -> Settings:
    db_path = database_path or Path(
        os.getenv("STOCK_AGENT_DB", str(PROJECT_ROOT / "data" / "portfolio.db"))
    )
    return Settings(
        project_root=PROJECT_ROOT,
        database_path=db_path,
        groq_api_key=os.getenv("GROQ_API_KEY") or None,
        groq_model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
        lseg_session_name=os.getenv("LSEG_SESSION", "desktop.workspace"),
        lseg_app_key=os.getenv("LSEG_APP_KEY") or None,
        lseg_session_timeout=float(os.getenv("LSEG_SESSION_TIMEOUT", "8")),
        lseg_request_timeout=float(os.getenv("LSEG_REQUEST_TIMEOUT", "20")),
    )
