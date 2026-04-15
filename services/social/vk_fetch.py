"""Загрузка постов со стены VK за последние `days` дней (VK API)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import httpx

from services.social.models import SocialPost

logger = logging.getLogger(__name__)

VK_API = "https://api.vk.com/method/"
VK_VERSION = "5.199"


def _screen_name_from_vk_url(url: str) -> str | None:
    try:
        p = urlparse(url.strip())
    except Exception:
        return None
    host = (p.netloc or "").lower().replace("www.", "")
    if host not in ("vk.com", "vk.ru", "m.vk.com"):
        return None
    path = (p.path or "").strip("/")
    if not path:
        return None
    return path.split("/")[0]


async def fetch_vk_posts(url: str, access_token: str, *, days: int = 2) -> list[SocialPost]:
    domain = _screen_name_from_vk_url(url)
    if not domain:
        logger.warning("VK: не удалось извлечь короткое имя из URL: %s", url)
        return []

    cutoff = datetime.now(tz=UTC) - timedelta(days=days)
    cutoff_ts = int(cutoff.timestamp())

    out: list[SocialPost] = []
    offset = 0

    async with httpx.AsyncClient(timeout=60.0) as client:
        while offset < 500:
            params: dict[str, Any] = {
                "access_token": access_token,
                "v": VK_VERSION,
                "domain": domain,
                "count": 100,
                "offset": offset,
                "filter": "owner",
            }
            r = await client.get(f"{VK_API}wall.get", params=params)
            r.raise_for_status()
            data = r.json()
            err = data.get("error")
            if err:
                logger.error("VK API error: %s", err)
                return out
            resp = data.get("response") or {}
            items = resp.get("items") or []
            if not items:
                break

            stop_wall = False
            for it in items:
                date_u = int(it.get("date", 0))
                if date_u < cutoff_ts:
                    stop_wall = True
                    break
                oid = it.get("owner_id")
                pid = it.get("id")
                if oid is None or pid is None:
                    continue
                text = (it.get("text") or "").strip()
                if not text:
                    continue
                ref = f"vk:{oid}_{pid}"
                wall_url = f"https://vk.com/wall{oid}_{pid}"
                pub = datetime.fromtimestamp(date_u, tz=UTC)
                out.append(
                    SocialPost(
                        ref=ref,
                        url=wall_url,
                        text=text,
                        published_at=pub,
                        source="vk",
                    )
                )

            if stop_wall:
                break
            if len(items) < 100:
                break
            offset += 100

    return out
