"""Application configuration loaded from the project's .env file."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import load_dotenv
from dotenv import set_key


PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
load_dotenv(ENV_PATH, override=False)

DEFAULT_GROQ_MODEL = "openai/gpt-oss-20b"
GROQ_MODEL_MIGRATIONS = {
    "llama-3.1-8b-instant": DEFAULT_GROQ_MODEL,
    "llama-3.3-70b-versatile": DEFAULT_GROQ_MODEL,
}


def normalize_groq_model(value: str | None) -> str:
    """Return a current model while preserving active explicit overrides."""
    configured = (value or "").strip() or DEFAULT_GROQ_MODEL
    return GROQ_MODEL_MIGRATIONS.get(configured, configured)


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
    supabase_url: str | None = None
    supabase_publishable_key: str | None = None


def get_settings(database_path: Path | None = None) -> Settings:
    db_path = database_path or Path(
        os.getenv("STOCK_AGENT_DB", str(PROJECT_ROOT / "data" / "portfolio.db"))
    )
    return Settings(
        project_root=PROJECT_ROOT,
        database_path=db_path,
        groq_api_key=os.getenv("GROQ_API_KEY") or None,
        groq_model=normalize_groq_model(os.getenv("GROQ_MODEL")),
        lseg_session_name=os.getenv("LSEG_SESSION", "desktop.workspace"),
        lseg_app_key=os.getenv("LSEG_APP_KEY") or None,
        lseg_session_timeout=float(os.getenv("LSEG_SESSION_TIMEOUT", "8")),
        lseg_request_timeout=float(os.getenv("LSEG_REQUEST_TIMEOUT", "20")),
        supabase_url=os.getenv("SUPABASE_URL") or None,
        supabase_publishable_key=os.getenv("SUPABASE_PUBLISHABLE_KEY") or None,
    )


def save_supabase_settings(
    url: str,
    publishable_key: str,
    *,
    env_path: Path = ENV_PATH,
) -> None:
    """Validate and persist non-secret Supabase project connection settings."""
    normalized_url = url.strip().rstrip("/")
    normalized_key = publishable_key.strip()
    if not normalized_url.startswith("https://") or ".supabase.co" not in normalized_url:
        raise ValueError("Enter the HTTPS project URL shown in Supabase Project Settings.")
    if not normalized_key:
        raise ValueError("Enter the Supabase publishable key.")

    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.touch(exist_ok=True)
    set_key(str(env_path), "SUPABASE_URL", normalized_url)
    set_key(str(env_path), "SUPABASE_PUBLISHABLE_KEY", normalized_key)
    os.environ["SUPABASE_URL"] = normalized_url
    os.environ["SUPABASE_PUBLISHABLE_KEY"] = normalized_key
