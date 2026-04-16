"""Загрузка постов по ссылкам агрегаторов, фильтр через Cerebras, запись в events."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from config import Settings
from database.db import Database
from services.cerebras.client import CerebrasService
from services.cerebras.event_extraction import (
    build_event_extraction_messages,
    parse_json_array_from_llm,
)
from services.event_requirements import has_date_and_place_for_storage
from services.social.fetch import fetch_posts_for_aggregator_url
from services.social.models import SocialPost

logger = logging.getLogger(__name__)


def _parse_iso_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    s = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


async def sync_social_events_for_all_links(
    db: Database,
    cerebras: CerebrasService,
    settings: Settings,
) -> int:
    """
    Для каждой строки aggregator_links: посты за N дней → Cerebras → upsert events.
    Возвращает суммарное число сохранённых/обновлённых мероприятий.
    """
    links = await db.list_all_aggregator_links()
    if not links:
        logger.info("Нет ссылок в aggregator_links — синхронизация мероприятий пропущена")
        return 0

    total_saved = 0
    for row in links:
        link_id = int(row["id"])
        city_id = int(row["city_id"]) if row.get("city_id") is not None else None
        url = str(row["url"]).strip()
        try:
            n = await _ingest_one_link(
                db,
                cerebras,
                settings,
                link_id=link_id,
                city_id=city_id,
                url=url,
            )
            total_saved += n
        except Exception:
            logger.exception("Ошибка обработки ссылки id=%s url=%s", link_id, url)

    return total_saved


async def _ingest_one_link(
    db: Database,
    cerebras: CerebrasService,
    settings: Settings,
    *,
    link_id: int,
    city_id: int | None,
    url: str,
) -> int:
    posts = await fetch_posts_for_aggregator_url(
        url,
        vk_access_token=settings.vk_access_token,
        days=settings.events_fetch_days,
    )
    logger.info(
        "Мероприятия: загружено постов с ссылки id=%s url=%s: %d (за %d дн.)",
        link_id,
        url,
        len(posts),
        settings.events_fetch_days,
    )
    if not posts:
        return 0

    by_ref: dict[str, SocialPost] = {p.ref: p for p in posts}
    messages = build_event_extraction_messages(posts)
    raw = await cerebras.extract_event_rows_from_posts_json(messages, temperature=0.2)
    try:
        rows = parse_json_array_from_llm(raw)
    except (ValueError, TypeError, UnicodeDecodeError) as e:
        logger.warning("Cerebras: не удалось разобрать JSON для %s: %s", url, e)
        return 0

    saved = 0
    for item in rows:
        ref = item.get("ref")
        if not isinstance(ref, str) or ref not in by_ref:
            continue
        post = by_ref[ref]
        title = (item.get("title") or "").strip() or post.text[:200]
        desc = (item.get("description_text") or "").strip() or post.text
        if len(desc) > 120:
            desc = desc[:117].rstrip() + "..."
        venue = item.get("venue_name")
        venue_name = venue.strip() if isinstance(venue, str) and venue.strip() else None
        addr = item.get("street_address")
        street_address = addr.strip() if isinstance(addr, str) and addr.strip() else None
        ek = item.get("event_kind")
        event_kind = ek.strip() if isinstance(ek, str) and ek.strip() else None
        starts_at = _parse_iso_datetime(item.get("starts_at"))

        if not has_date_and_place_for_storage(starts_at, venue_name, street_address):
            logger.debug(
                "Пропуск мероприятия ref=%s: нет даты начала или места (площадка/адрес)",
                ref,
            )
            continue

        external_key = f"{link_id}:{ref}"
        if len(external_key) > 2000:
            external_key = external_key[:2000]

        await db.upsert_event(
            city_id=city_id,
            aggregator_link_id=link_id,
            source=post.source,
            external_key=external_key,
            title=title,
            description_text=desc,
            event_kind=event_kind,
            starts_at=starts_at,
            ends_at=None,
            venue_name=venue_name,
            venue_url=None,
            street_address=street_address,
            price_amount=None,
            currency=None,
            source_url=post.url,
            image_url=None,
            raw_json={"llm": item, "post_ref": ref},
        )
        saved += 1

    return saved
