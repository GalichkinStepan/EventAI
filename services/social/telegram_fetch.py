"""Публичная лента канала: https://t.me/s/username (без Bot API)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from services.social.models import SocialPost

logger = logging.getLogger(__name__)

TG_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5",
}


def _channel_from_telegram_url(url: str) -> str | None:
    try:
        p = urlparse(url.strip())
    except Exception:
        return None
    host = (p.netloc or "").lower().replace("www.", "")
    if host not in ("t.me", "telegram.me", "telegram.dog"):
        return None
    parts = [x for x in (p.path or "").split("/") if x]
    if not parts:
        return None
    if parts[0] == "s" and len(parts) >= 2:
        return parts[1]
    if parts[0].startswith("+"):
        return None
    return parts[0]


def _parse_tg_datetime(el) -> datetime | None:  # type: ignore[no-untyped-def]
    t = el.find("time")
    if t is not None and t.get("datetime"):
        raw = t["datetime"].strip()
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            pass
    return None


async def fetch_telegram_public_posts(url: str, *, days: int = 2) -> list[SocialPost]:
    channel = _channel_from_telegram_url(url)
    if not channel:
        logger.warning("Telegram: не удалось извлечь @канал из URL: %s", url)
        return []

    cutoff = datetime.now(tz=UTC) - timedelta(days=days)
    base = f"https://t.me/s/{channel}"
    out: list[SocialPost] = []
    seen_refs: set[str] = set()
    before: int | None = None

    async with httpx.AsyncClient(headers=TG_HEADERS, follow_redirects=True, timeout=60.0) as client:
        for _ in range(40):
            page_url = f"{base}?before={before}" if before is not None else base
            r = await client.get(page_url)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            wraps = soup.select(".tgme_widget_message_wrap")
            if not wraps:
                break

            page_dates: list[datetime] = []
            min_mid: int | None = None

            for wrap in wraps:
                msg = wrap.select_one(".tgme_widget_message")
                if msg is None:
                    continue
                data_post = msg.get("data-post") or ""
                if "/" not in data_post:
                    continue
                ch, mid_s = data_post.split("/", 1)
                if not mid_s.isdigit():
                    continue
                mid = int(mid_s)
                if min_mid is None or mid < min_mid:
                    min_mid = mid

                ref = f"tg:{ch}/{mid_s}"
                if ref in seen_refs:
                    continue

                dt_el = wrap.select_one(".tgme_widget_message_date") or msg
                pub = _parse_tg_datetime(dt_el)
                if pub is None:
                    continue
                if pub.tzinfo is None:
                    pub = pub.replace(tzinfo=UTC)
                else:
                    pub = pub.astimezone(UTC)
                page_dates.append(pub)

                if pub < cutoff:
                    continue

                text_el = wrap.select_one(".tgme_widget_message_text")
                text = text_el.get_text("\n", strip=True) if text_el else ""
                text = (text or "").strip()
                if not text:
                    continue

                seen_refs.add(ref)
                post_url = f"https://t.me/{ch}/{mid_s}"
                out.append(
                    SocialPost(
                        ref=ref,
                        url=post_url,
                        text=text,
                        published_at=pub,
                        source="telegram",
                    )
                )

            if min_mid is None:
                break

            # Все посты на странице старее окна — старее страницы не нужны
            if page_dates and max(page_dates) < cutoff:
                break

            before = min_mid - 1

    return out
