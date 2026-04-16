"""Проверка обязательных полей мероприятия: дата/время и место (для БД и промпта Cerebras)."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def has_date_and_place_for_storage(
    starts_at: datetime | None,
    venue_name: str | None,
    street_address: str | None,
) -> bool:
    """Дата начала обязательна; место — хотя бы название площадки или адрес (непустая строка)."""
    if starts_at is None:
        return False
    v = venue_name.strip() if isinstance(venue_name, str) else ""
    a = street_address.strip() if isinstance(street_address, str) else ""
    return bool(v or a)


def event_dict_has_date_and_place(event: dict[str, Any]) -> bool:
    """Те же правила для записи из БД (asyncpg отдаёт starts_at как datetime)."""
    sa = event.get("starts_at")
    if sa is None or not isinstance(sa, datetime):
        return False
    v = event.get("venue_name")
    a = event.get("street_address")
    return has_date_and_place_for_storage(
        sa,
        v if isinstance(v, str) else None,
        a if isinstance(a, str) else None,
    )
