import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse, urlunparse

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


def _normalize_webhook_url(url: str) -> tuple[str, str]:
    """Возвращает (полный URL для setWebhook, path для aiohttp)."""
    u = urlparse(url.strip())
    if u.scheme not in ("https", "http"):
        raise RuntimeError(
            "WEBHOOK_URL должен начинаться с https:// или http:// (для продакшена нужен https)."
        )
    if not u.netloc:
        raise RuntimeError(
            "WEBHOOK_URL: укажите хост, например https://mybot.onrender.com/webhook"
        )
    path = u.path or "/webhook"
    if path == "/":
        path = "/webhook"
    full = urlunparse((u.scheme, u.netloc, path, "", "", ""))
    return full, path


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
    use_webhook: bool = False
    webhook_url: str | None = None
    webhook_path: str = "/webhook"
    webhook_secret: str | None = None
    webhook_port: int = 8080


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

    webhook_url_raw = os.getenv("WEBHOOK_URL", "").strip()
    webhook_secret = os.getenv("WEBHOOK_SECRET", "").strip() or None

    # Локальная разработка: polling даже если в .env остался старый WEBHOOK_URL
    use_polling_override = os.getenv("USE_POLLING", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )

    port_str = os.getenv("PORT", "8080").strip()
    try:
        webhook_port = int(port_str)
    except ValueError:
        webhook_port = 8080
    if webhook_port < 1 or webhook_port > 65535:
        webhook_port = 8080

    use_webhook = False
    webhook_url: str | None = None
    webhook_path = "/webhook"
    if webhook_url_raw and not use_polling_override:
        webhook_url, webhook_path = _normalize_webhook_url(webhook_url_raw)
        use_webhook = True

    _is_render = os.getenv("RENDER", "").strip().lower() in ("true", "1", "yes")
    if _is_render and use_polling_override:
        raise RuntimeError(
            "На Render нельзя USE_POLLING=1: нужен webhook и ответ на HTTP (health check). "
            "Уберите USE_POLLING и задайте WEBHOOK_URL."
        )
    if _is_render and not use_webhook:
        raise RuntimeError(
            "На Render нужен webhook: задайте в переменных окружения WEBHOOK_URL вида "
            "https://<имя-сервиса>.onrender.com/webhook (HTTPS). "
            "PORT задаёт Render сам — слушаем его в приложении. "
            "Для локального polling задайте USE_POLLING=1 и не используйте RENDER=true."
        )
    if _is_render and webhook_url is not None and not str(webhook_url).lower().startswith(
        "https://"
    ):
        raise RuntimeError(
            "На Render WEBHOOK_URL должен быть с https:// (Telegram принимает только HTTPS в продакшене)."
        )

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
        use_webhook=use_webhook,
        webhook_url=webhook_url,
        webhook_path=webhook_path,
        webhook_secret=webhook_secret,
        webhook_port=webhook_port,
    )
