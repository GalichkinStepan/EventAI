"""Маршрутизация URL агрегатора к загрузчику постов (VK / Telegram)."""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from services.social.models import SocialPost
from services.social.telegram_fetch import fetch_telegram_public_posts
from services.social.vk_fetch import fetch_vk_posts

logger = logging.getLogger(__name__)


async def fetch_posts_for_aggregator_url(
    url: str,
    *,
    vk_access_token: str | None,
    days: int = 2,
) -> list[SocialPost]:
    u = url.strip()
    try:
        host = urlparse(u).netloc.lower()
    except Exception:
        host = ""

    if "vk.com" in host or "vk.ru" in host:
        if not vk_access_token:
            logger.warning("VK_ACCESS_TOKEN не задан — пропуск VK-ссылки: %s", url)
            return []
        return await fetch_vk_posts(u, vk_access_token, days=days)

    if "t.me" in host or "telegram.me" in host or "telegram.dog" in host:
        return await fetch_telegram_public_posts(u, days=days)

    logger.warning("Неизвестный тип ссылки (нужен VK или Telegram): %s", url)
    return []
