"""Модификация пользовательского текста в сообщения для Chat Completions (Cerebras)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from keyboards.categories import EVENT_CATEGORIES

DEFAULT_SYSTEM_PROMPT = (
    "Ты помощник по подбору мероприятий и ответам на вопросы пользователя. "
    "Учитывай город и интересы из контекста. "
    "Для пользователя «сегодня», «завтра» и время суток определяй по часовому поясу города из контекста (IANA), "
    "а не по UTC. "
    "Если в сообщении приведён список мероприятий в городе (на сегодня и позже по местному времени), "
    "опирайся на него для рекомендаций, сравнений и ответов по афише. "
    "Отвечай по делу, на русском языке. "
    "Длина ответа — не более 4000 символов (включая пробелы и переносы строк)."
)


def _zoneinfo_safe(tz_name: str | None) -> ZoneInfo | None:
    if not tz_name or not str(tz_name).strip():
        return None
    try:
        return ZoneInfo(str(tz_name).strip())
    except Exception:
        return None


def _local_time_context_line(tz_name: str | None) -> str:
    z = _zoneinfo_safe(tz_name)
    if z is None:
        return ""
    now = datetime.now(z)
    return (
        f"\n[Часовой пояс города (IANA): {tz_name.strip()}; "
        f"текущие дата и время в этом поясе: {now.strftime('%Y-%m-%d %H:%M')}]"
    )


def _format_starts_at(value: Any, display_tz: ZoneInfo | None) -> str:
    if value is None:
        return "—"
    if isinstance(value, datetime):
        if display_tz is not None and value.tzinfo is not None:
            try:
                local = value.astimezone(display_tz)
                return local.strftime("%Y-%m-%d %H:%M")
            except Exception:
                pass
        return value.isoformat(sep=" ", timespec="minutes")
    return str(value)


def _format_upcoming_events_block(
    events: list[dict[str, Any]],
    *,
    city_timezone: str | None,
) -> str:
    display_tz = _zoneinfo_safe(city_timezone)
    lines: list[str] = []
    for i, e in enumerate(events, 1):
        title = (e.get("title") or "").strip() or "—"
        desc = (e.get("description_text") or "").strip()
        if len(desc) > 200:
            desc = desc[:197] + "..."
        kind = (e.get("event_kind") or "").strip()
        venue = (e.get("venue_name") or "").strip()
        addr = (e.get("street_address") or "").strip()
        url = (e.get("source_url") or "").strip()
        when = _format_starts_at(e.get("starts_at"), display_tz)
        parts = [f"{i}. {title}", f"дата/время: {when}"]
        if kind:
            parts.append(f"тип: {kind}")
        if venue:
            parts.append(f"площадка: {venue}")
        if addr:
            parts.append(f"адрес: {addr}")
        if desc:
            parts.append(f"описание: {desc}")
        if url:
            parts.append(f"ссылка: {url}")
        lines.append("\n".join(parts))
    return "\n\n".join(lines)


def build_chat_messages(
    raw_user_prompt: str,
    *,
    city: str,
    interests: list[str],
    system_prompt: str | None = None,
    aggregator_links: list[tuple[str, str]] | None = None,
    upcoming_events: list[dict[str, Any]] | None = None,
    city_timezone: str | None = None,
) -> list[dict[str, Any]]:
    """
    Оборачивает сырой текст пользователя в структуру сообщений для API:
    system — роль ассистента; user — обогащённый контекстом запрос.
    aggregator_links: (title, url) из БД для города пользователя.
    upcoming_events: мероприятия города из БД (дата начала >= сегодня по календарю city_timezone).
    city_timezone: IANA (например Europe/Moscow) для «сейчас» и отображения времени мероприятий.
    """
    labels = [EVENT_CATEGORIES.get(c, c) for c in interests]
    interests_line = ", ".join(labels) if labels else "не указаны"

    tz_line = _local_time_context_line(city_timezone)

    agg_block = ""
    if aggregator_links:
        lines = "\n".join(f"- {t}: {u}" for t, u in aggregator_links)
        agg_block = f"\n\n[Агрегаторы мероприятий для этого города]\n{lines}"

    events_block = ""
    if upcoming_events:
        events_block = (
            "\n\n[Мероприятия в этом городе из базы "
            "(дата начала по календарю города не раньше сегодняшнего дня в часовом поясе города)]\n"
            + _format_upcoming_events_block(upcoming_events, city_timezone=city_timezone)
        )

    enriched_user = (
        f"[Контекст профиля: город — {city}; интересы — {interests_line}]{tz_line}"
        f"{agg_block}{events_block}\n\n"
        f"Запрос пользователя:\n{raw_user_prompt.strip()}"
    )

    return [
        {"role": "system", "content": system_prompt or DEFAULT_SYSTEM_PROMPT},
        {"role": "user", "content": enriched_user},
    ]
