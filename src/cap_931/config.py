import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


@dataclass
class Settings:
    openai_api_key: str = field(default_factory=lambda: _env("OPENAI_API_KEY"))
    # Cheap/fast model for extraction agents; stronger model for the writer and verifier.
    fast_model: str = field(default_factory=lambda: _env("OPENAI_FAST_MODEL", "gpt-5-mini"))
    smart_model: str = field(default_factory=lambda: _env("OPENAI_SMART_MODEL", "gpt-5"))
    tavily_api_key: str = field(default_factory=lambda: _env("TAVILY_API_KEY"))
    # The SEC asks automated clients to identify themselves with a contact address.
    sec_user_agent: str = field(
        default_factory=lambda: _env("SEC_USER_AGENT", "cap-931-sales-agent research@example.com")
    )
    smtp_host: str = field(default_factory=lambda: _env("SMTP_HOST", "smtp.gmail.com"))
    smtp_port: int = field(default_factory=lambda: int(_env("SMTP_PORT", "587")))
    smtp_user: str = field(default_factory=lambda: _env("SMTP_USER"))
    smtp_password: str = field(default_factory=lambda: _env("SMTP_PASSWORD"))
    cache_dir: Path = field(default_factory=lambda: Path(_env("CACHE_DIR", str(PROJECT_ROOT / ".cache"))))
    cache_ttl_hours: int = field(default_factory=lambda: int(_env("CACHE_TTL_HOURS", "24")))
    alerts_db: Path = field(default_factory=lambda: Path(_env("ALERTS_DB", str(PROJECT_ROOT / "alerts.db"))))
    # Name printed on the Per Scholas template cover of generated documents.
    learner_name: str = field(default_factory=lambda: _env("LEARNER_NAME", "Olanrewaju Muili"))
    max_evidence_chars: int = 3000

    @property
    def search_provider(self) -> str:
        return "tavily" if self.tavily_api_key else "duckduckgo"


def get_settings() -> Settings:
    return Settings()
