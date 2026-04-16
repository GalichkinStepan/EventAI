"""Модификация пользовательского текста в сообщения для Chat Completions (Cerebras)."""

from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

from keyboards.categories import EVENT_CATEGORIES
from services.event_requirements import event_dict_has_date_and_place

# --- Логика подбора мероприятий для пользователя (читать в первую очередь) ---
#
# Период афиши
#   В промпт попадают только мероприятия из базы с датой начала в СЛЕДУЮЩЕМ календарном
#   месяце относительно «сегодня» в часовом поясе города пользователя (например, если
#   сейчас апрель — берётся май).
#
# Два режима (смотрим на время последней реплики в этом чате с ботом — user или assistant):
#
# 1) «Активный диалог» — последнее сообщение в переписке было не позднее 24 часов назад,
#    ЛИБО пользователь ещё ни разу не писал боту (первое обращение: истории ещё нет).
#    Тогда в промпт подмешивается до 10 мероприятий: до 8, которые по тексту похожи на
#    выбранные в профиле интересы (музыка, спорт, …), и до 2 «для разнообразия» среди
#    остальных в том же месяце. Лишние кандидаты отбрасываются случайно при избытке.
#
# 2) «Диалог на паузе» — с последней реплики прошло больше 24 часов.
#    Тогда в промпт попадают только 2 случайных мероприятия из того же месяца, которые
#    мы ещё НЕ показывали этому пользователю в подборе (учёт в таблице cerebras_suggested_events).
#    Если все мероприятия месяца уже были показаны — блок с карточками пустой, текстом
#    объясняем, что новых нет.
#
# После успешного ответа Cerebras id показанных мероприятий сохраняются, чтобы не
# повторять их в режиме «2 новых» после долгой паузы.
# ----------------------------------------------------------------------------

DEFAULT_SYSTEM_PROMPT = (
    "Ты помощник по подбору мероприятий и ответам на вопросы пользователя. "
    "Ниже может быть история переписки: учитывай её, чтобы ответы были связными. "
    "Последнее сообщение пользователя содержит актуальный контекст (город, интересы, афишу) и новый запрос — опирайся на него в первую очередь. "
    "Учитывай город и интересы из контекста. "
    "Для пользователя «сегодня», «завтра» и время суток определяй по часовому поясу города из контекста (IANA), а не по UTC. "
    "Список мероприятий относится к следующему календарному месяцу в часовом поясе города. "
    "Если в контексте много карточек — пользователь недавно общался с ботом; если мало — давно не писал, показываем только свежие для него позиции. "
    "Когда рекомендуешь или описываешь конкретное мероприятие из карточек в контексте, обязательно укажи: дату и время начала, место (площадку или адрес) и ссылку на мероприятие — возьми из карточки; если в карточке чего-то нет, кратко скажи, что в источнике нет этих данных. "
    "Отвечай по делу, на русском языке. "
    "Длина ответа — не более 4000 символов (включая пробелы и переносы строк)."
)

# Ключевые подстроки для сопоставления free-text event_kind/описания с id интересов из профиля
INTEREST_KEYWORDS: dict[str, tuple[str, ...]] = {
    "music": (
        "музык",
        "концерт",
        "dj",
        "диджей",
        "клуб",
        "джаз",
        "рок",
        "поп",
        "певиц",
        "певец",
        "оркестр",
        "фестивал",
        "фест",
        "альбом",
        "сингл",
    ),
    "sport": (
        "спорт",
        "марафон",
        "футбол",
        "йог",
        "йога",
        "трениров",
        "бег",
        "фитнес",
        "велосипед",
        "лыж",
        "плаван",
        "единоборств",
        "чемпионат",
    ),
    "theater": (
        "театр",
        "спектакл",
        "кино",
        "фильм",
        "кинотеатр",
        "премьер",
        "режисс",
        "актер",
        "актрис",
        "балет",
        "опер",
    ),
    "education": (
        "лекци",
        "образован",
        "курс",
        "семинар",
        "мастер-класс",
        "школ",
        "тренинг",
        "воркшоп",
        "webinar",
        "вебинар",
    ),
    "networking": (
        "нетворк",
        "networking",
        "meetup",
        "митап",
        "комьюнити",
        "стартап",
        "питч",
        "инвест",
    ),
    "festivals": (
        "фестивал",
        "ярмарк",
        "фест",
        "ярмарка",
        "выставк",
        "экспо",
    ),
}

MAX_EVENTS_BY_INTERESTS = 8
MAX_EVENTS_EXTRA = 2
FULL_MODE_TOTAL = MAX_EVENTS_BY_INTERESTS + MAX_EVENTS_EXTRA  # 10
MINIMAL_MODE_COUNT = 2

DIALOG_RECENT_HOURS = 24

EventPromptFormat = Literal[
    "none",
    "empty_pool",
    "full",
    "minimal",
    "minimal_exhausted",
]


def next_calendar_month_utc_bounds(tz_name: str) -> tuple[datetime, datetime]:
    """
    Следующий календарный месяц в поясе tz_name.
    Возвращает (начало UTC включительно, конец UTC исключительно).
    """
    z = ZoneInfo(tz_name.strip())
    now = datetime.now(z)
    y, m = now.year, now.month
    if m == 12:
        ny, nm = y + 1, 1
    else:
        ny, nm = y, m + 1
    start_local = datetime(ny, nm, 1, 0, 0, 0, tzinfo=z)
    if nm == 12:
        end_y, end_m = ny + 1, 1
    else:
        end_y, end_m = ny, nm + 1
    end_local_exclusive = datetime(end_y, end_m, 1, 0, 0, 0, tzinfo=z)
    return (
        start_local.astimezone(timezone.utc),
        end_local_exclusive.astimezone(timezone.utc),
    )


def event_matches_interests(event: dict[str, Any], interest_ids: list[str]) -> bool:
    blob = (
        f"{event.get('event_kind') or ''} "
        f"{event.get('title') or ''} "
        f"{event.get('description_text') or ''}"
    ).lower()
    for iid in interest_ids:
        for kw in INTEREST_KEYWORDS.get(iid, ()):
            if kw in blob:
                return True
    return False


def pick_events_for_cerebras_chat(
    events: list[dict[str, Any]],
    interest_ids: list[str],
    *,
    max_matching: int = MAX_EVENTS_BY_INTERESTS,
    max_extra: int = MAX_EVENTS_EXTRA,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Режим «активный диалог»: до max_matching событий по интересам и до max_extra — «вне интересов».
    Итого до 10 штук. При отсутствии интересов — случайные 8+2 из пула месяца.
    """
    pool = list(events)
    random.shuffle(pool)
    if not interest_ids:
        a = pool[:max_matching]
        b = pool[max_matching : max_matching + max_extra]
        return a, b

    matching = [e for e in pool if event_matches_interests(e, interest_ids)]
    non_matching = [e for e in pool if not event_matches_interests(e, interest_ids)]
    random.shuffle(matching)
    random.shuffle(non_matching)
    return matching[:max_matching], non_matching[:max_extra]


def select_events_for_cerebras_prompt(
    pool: list[dict[str, Any]],
    interest_ids: list[str],
    *,
    dialog_recent_within_24h: bool,
    suggested_event_ids: set[int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[int], EventPromptFormat]:
    """
    Собирает карточки мероприятий для одного запроса к Cerebras и id для записи в БД.

    См. модульный докстринг в начале файла: условия 24 часов, 10 vs 2, учёт уже показанных.
    """
    pool = [e for e in pool if event_dict_has_date_and_place(e)]
    if not pool:
        return [], [], [], "empty_pool"

    if dialog_recent_within_24h:
        m, x = pick_events_for_cerebras_chat(
            pool,
            interest_ids,
            max_matching=MAX_EVENTS_BY_INTERESTS,
            max_extra=MAX_EVENTS_EXTRA,
        )
        ids: list[int] = []
        for e in m + x:
            if e.get("id") is not None:
                ids.append(int(e["id"]))
        return m, x, ids, "full"

    unseen = [
        e
        for e in pool
        if e.get("id") is not None and int(e["id"]) not in suggested_event_ids
    ]
    random.shuffle(unseen)
    if not unseen:
        return [], [], [], "minimal_exhausted"

    take = unseen[:MINIMAL_MODE_COUNT]
    out_ids = [int(e["id"]) for e in take if e.get("id") is not None]
    return take, [], out_ids, "minimal"


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


def _format_event_one_line_where(event: dict[str, Any], *, max_len: int = 72) -> str | None:
    """Одна строка «где»: не дублируем площадку и адрес, если один уже входит в другой."""
    venue = (event.get("venue_name") or "").strip()
    addr = (event.get("street_address") or "").strip()
    if not venue and not addr:
        return None
    if venue and addr:
        if venue in addr or addr in venue:
            where = addr if len(addr) >= len(venue) else venue
        else:
            where = f"{venue}, {addr}"
    else:
        where = venue or addr
    if len(where) > max_len:
        return where[: max_len - 1] + "…"
    return where


def _format_upcoming_events_block(
    events: list[dict[str, Any]],
    *,
    city_timezone: str | None,
    start_index: int = 1,
) -> str:
    """
    Передача в модель: название, время и ссылка, при наличии — краткое описание (до 200 симв.),
    одна строка «где». Отдельно не дублируем «тип» и пару площадка/адрес — только объединённое «где».
    """
    display_tz = _zoneinfo_safe(city_timezone)
    lines: list[str] = []
    for i, e in enumerate(events, start_index):
        title = (e.get("title") or "").strip() or "—"
        desc = (e.get("description_text") or "").strip()
        if len(desc) > 200:
            desc = desc[:197] + "..."
        url = (e.get("source_url") or "").strip()
        when = _format_starts_at(e.get("starts_at"), display_tz)
        where = _format_event_one_line_where(e)
        second = f"{when}"
        if url:
            second = f"{when} · {url}"
        block = f"{i}. {title}\n   {second}"
        if desc:
            block += f"\n   описание: {desc}"
        if where:
            block += f"\n   где: {where}"
        lines.append(block)
    return "\n\n".join(lines)


def _format_events_for_prompt(
    matching: list[dict[str, Any]],
    extra: list[dict[str, Any]],
    *,
    city_timezone: str | None,
    mode: Literal["full", "minimal"],
) -> str:
    if mode == "minimal":
        if not matching:
            return ""
        return (
            "[Мероприятия на следующий месяц — после паузы в переписке показываем до 2 новых "
            "карточек, которые раньше не попадали вам в подбор]\n"
            + _format_upcoming_events_block(matching, city_timezone=city_timezone, start_index=1)
        )

    parts: list[str] = []
    if matching:
        parts.append(
            "[Мероприятия на следующий календарный месяц — по вашим интересам (до 8)]\n"
            + _format_upcoming_events_block(matching, city_timezone=city_timezone, start_index=1)
        )
    if extra:
        start = len(matching) + 1
        parts.append(
            "[На тот же период — вне интересов, для разнообразия (до 2)]\n"
            + _format_upcoming_events_block(extra, city_timezone=city_timezone, start_index=start)
        )
    return "\n\n".join(parts)


def build_chat_messages(
    raw_user_prompt: str,
    *,
    city: str,
    interests: list[str],
    history: list[tuple[str, str]] | None = None,
    system_prompt: str | None = None,
    aggregator_links: list[tuple[str, str]] | None = None,
    city_timezone: str | None = None,
    event_matching: list[dict[str, Any]] | None = None,
    event_extra: list[dict[str, Any]] | None = None,
    event_format: EventPromptFormat = "none",
) -> list[dict[str, Any]]:
    """
    system + опционально история + финальный user с контекстом и запросом.

    Карточки мероприятий заранее отобраны через select_events_for_cerebras_prompt (см. логику в модуле).
    """
    labels = [EVENT_CATEGORIES.get(c, c) for c in interests]
    interests_line = ", ".join(labels) if labels else "не указаны"

    tz_line = _local_time_context_line(city_timezone)

    agg_block = ""
    if aggregator_links:
        lines = "\n".join(f"- {t}: {u}" for t, u in aggregator_links)
        agg_block = f"\n\n[Агрегаторы мероприятий для этого города]\n{lines}"

    events_block = ""
    if event_format == "none":
        pass
    elif event_format == "empty_pool":
        events_block = (
            "\n\n[Мероприятия на следующий календарный месяц в базе для этого города не найдены]"
        )
    elif event_format == "minimal_exhausted":
        events_block = (
            "\n\n[Все мероприятия следующего месяца уже попадали вам в подбор ранее. "
            "Новых карточек для краткого напоминания афиши сейчас нет.]"
        )
    elif event_format == "full":
        em = event_matching or []
        ex = event_extra or []
        if em or ex:
            events_block = "\n\n" + _format_events_for_prompt(
                em, ex, city_timezone=city_timezone, mode="full"
            )
        else:
            events_block = (
                "\n\n[Мероприятия на следующий календарный месяц в базе для этого города не найдены]"
            )
    elif event_format == "minimal":
        em = event_matching or []
        if em:
            events_block = "\n\n" + _format_events_for_prompt(
                em, [], city_timezone=city_timezone, mode="minimal"
            )
        else:
            events_block = (
                "\n\n[Новых мероприятий на следующий месяц для вас не осталось — все уже были в подборе.]"
            )

    enriched_user = (
        f"[Контекст профиля: город — {city}; интересы — {interests_line}]{tz_line}"
        f"{agg_block}{events_block}\n\n"
        f"Запрос пользователя:\n{raw_user_prompt.strip()}"
    )

    out: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt or DEFAULT_SYSTEM_PROMPT},
    ]
    if history:
        for role, content in history:
            if role in ("user", "assistant") and content.strip():
                out.append({"role": role, "content": content})
    out.append({"role": "user", "content": enriched_user})
    return out
