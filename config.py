import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Загружаем .env из каталога проекта, а не из cwd (иначе админы и токен не подхватятся).
_PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(_PROJECT_ROOT / ".env")


def _parse_admin_ids(raw: str) -> frozenset[int]:
    raw = raw.lstrip("\ufeff").strip()
    if not raw:
        return frozenset()
    out: set[int] = set()
    for part in re.split(r"[,;|\s]+", raw):
        part = part.strip().strip("'\"")
        if part.isdigit():
            out.add(int(part))
    return frozenset(out)


def _collect_admin_telegram_ids() -> frozenset[int]:
    """ADMIN_TELEGRAM_IDS или один id в TELEGRAM_ADMIN_ID / ADMIN_TELEGRAM_ID / ADMIN_ID / TELEGRAM_ID."""
    primary = os.getenv("ADMIN_TELEGRAM_IDS", "")
    merged = _parse_admin_ids(primary)
    if merged:
        return merged
    for key in (
        "TELEGRAM_ADMIN_ID",
        "ADMIN_TELEGRAM_ID",
        "ADMIN_ID",
        "TELEGRAM_ID",
    ):
        extra = os.getenv(key, "")
        merged = _parse_admin_ids(extra)
        if merged:
            return merged
    return frozenset()


@dataclass(frozen=True)
class Settings:
    bot_token: str
    database_url: str
    admin_telegram_ids: frozenset[int]
    cerebras_api_key: str | None = None
    cerebras_model: str = "llama3.1-8b"
    vk_access_token: str | None = None
    events_sync_enabled: bool = True
    events_sync_interval_hours: int = 12
    events_fetch_days: int = 2


def load_settings() -> Settings:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "Не задан BOT_TOKEN. Создайте файл .env по образцу .env.example."
        )
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError(
            "Не задан DATABASE_URL (PostgreSQL). См. .env.example."
        )
    cerebras_key = os.getenv("CEREBRAS_API_KEY", "").strip() or None
    cerebras_model = os.getenv("CEREBRAS_MODEL", "llama3.1-8b").strip()
    if not cerebras_model:
        cerebras_model = "llama3.1-8b"
    admin_ids = _collect_admin_telegram_ids()

    vk_access_token = os.getenv("VK_ACCESS_TOKEN", "").strip() or None

    events_sync_enabled = os.getenv("EVENTS_SYNC_ENABLED", "1").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    try:
        ev_hours = int(os.getenv("EVENTS_SYNC_INTERVAL_HOURS", "12").strip() or "12")
    except ValueError:
        ev_hours = 12
    if ev_hours < 1:
        ev_hours = 12
    try:
        ev_days = int(os.getenv("EVENTS_FETCH_DAYS", "2").strip() or "2")
    except ValueError:
        ev_days = 2
    if ev_days < 1:
        ev_days = 2

    return Settings(
        bot_token=token,
        database_url=database_url,
        admin_telegram_ids=admin_ids,
        cerebras_api_key=cerebras_key,
        cerebras_model=cerebras_model,
        vk_access_token=vk_access_token,
        events_sync_enabled=events_sync_enabled,
        events_sync_interval_hours=ev_hours,
        events_fetch_days=ev_days,
    )
