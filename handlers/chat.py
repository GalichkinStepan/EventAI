"""Сообщения после завершения регистрации: текст пользователя → промпт для Cerebras."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.types import Message

from database.db import Database
from keyboards.categories import EVENT_CATEGORIES
from services.cerebras import (
    CerebrasPaymentRequiredError,
    CerebrasService,
    build_chat_messages,
)
from services.cerebras.prompt_builder import (
    DIALOG_RECENT_HOURS,
    EventPromptFormat,
    next_calendar_month_utc_bounds,
    select_events_for_cerebras_prompt,
)
from states.user_preferences import ProfileSetup

logger = logging.getLogger(__name__)

# Пары user+assistant в истории (лимит сообщений ролей, не байты)
_MAX_CEREBRAS_HISTORY_MESSAGES = 24

# Лимит Telegram на длину одного сообщения
_MAX_MESSAGE_LEN = 4096

router = Router(name="chat")


def _split_reply(text: str) -> list[str]:
    """Разбивает текст на части ≤4096 символов (Unicode-safe)."""
    if len(text) <= _MAX_MESSAGE_LEN:
        return [text]
    return [text[i : i + _MAX_MESSAGE_LEN] for i in range(0, len(text), _MAX_MESSAGE_LEN)]


async def _send_plain_reply(message: Message, text: str) -> None:
    """Ответ без parse_mode: текст модели не должен интерпретироваться как разметка Telegram."""
    for part in _split_reply(text):
        await message.answer(part, parse_mode=None)


def _is_dialog_recent_within_hours(last_activity_utc: datetime | None, *, hours: int) -> bool:
    """
    Активный диалог: нет истории (первое сообщение) или последняя реплика не старше `hours` часов.
    Время сравниваем в UTC.
    """
    if last_activity_utc is None:
        return True
    now = datetime.now(timezone.utc)
    la = last_activity_utc
    if la.tzinfo is None:
        la = la.replace(tzinfo=timezone.utc)
    delta = now - la.astimezone(timezone.utc)
    return delta <= timedelta(hours=hours)


@router.message(
    F.text,
    ~F.text.startswith("/"),
    ~StateFilter(ProfileSetup),
)
async def user_prompt_to_cerebras(
    message: Message,
    db: Database,
    cerebras: CerebrasService | None,
) -> None:
    if not message.from_user:
        return

    user_id = message.from_user.id
    if not await db.is_profile_complete(user_id):
        await message.answer(
            "Сначала завершите настройку профиля: отправьте /start и укажите город и интересы."
        )
        return

    if cerebras is None:
        await message.answer(
            "Ответы через ИИ сейчас недоступны: задайте CEREBRAS_API_KEY в настройках бота."
        )
        return

    raw = (message.text or "").strip()
    if not raw:
        return

    row = await db.get_user(user_id)
    if not row:
        await message.answer("Профиль не найден. Начните с /start.")
        return

    city = str(row.get("city") or "").strip()
    interests = row.get("interests") or []
    if not isinstance(interests, list):
        interests = []

    agg: list[tuple[str, str]] = []
    city_tz: str | None = None
    cid = row.get("city_id")

    last_activity = await db.get_last_cerebras_activity_at(user_id)
    suggested_ids = await db.get_cerebras_suggested_event_ids(user_id)
    dialog_recent = _is_dialog_recent_within_hours(last_activity, hours=DIALOG_RECENT_HOURS)

    history_rows = await db.list_recent_cerebras_chat_turns(
        user_id, limit=_MAX_CEREBRAS_HISTORY_MESSAGES
    )
    history = [(str(r["role"]), str(r["content"])) for r in history_rows]

    event_matching: list[dict] = []
    event_extra: list[dict] = []
    event_format: EventPromptFormat = "none"
    suggested_to_record: list[int] = []

    if cid is not None:
        cid_int = int(cid)
        raw_tz = row.get("city_timezone")
        city_tz = (
            str(raw_tz).strip()
            if isinstance(raw_tz, str) and raw_tz.strip()
            else "Europe/Moscow"
        )
        for link in await db.list_aggregators_for_city(cid_int):
            agg.append((str(link["title"]), str(link["url"])))
        start_utc, end_excl = next_calendar_month_utc_bounds(city_tz)
        upcoming = await db.list_events_in_time_range_for_city(
            cid_int,
            start_utc=start_utc,
            end_utc_exclusive=end_excl,
        )
        event_matching, event_extra, suggested_to_record, event_format = (
            select_events_for_cerebras_prompt(
                upcoming,
                interests,
                dialog_recent_within_24h=dialog_recent,
                suggested_event_ids=suggested_ids,
            )
        )

    messages = build_chat_messages(
        raw,
        city=city,
        interests=interests,
        history=history,
        aggregator_links=agg or None,
        city_timezone=city_tz,
        event_matching=event_matching,
        event_extra=event_extra,
        event_format=event_format,
    )
    try:
        reply = await cerebras.complete(messages)
    except CerebrasPaymentRequiredError:
        await message.answer(
            "Ответы ИИ временно недоступны: на аккаунте Cerebras требуется оплата или недостаточно средств. "
            "Проверьте биллинг: https://cloud.cerebras.ai/"
        )
        return
    except Exception:
        await message.answer("Не удалось получить ответ. Попробуйте позже.")
        return

    if not reply:
        reply = "Пустой ответ модели."

    try:
        await db.append_cerebras_exchange(user_id, raw, reply)
    except Exception:
        logger.exception("Не удалось сохранить историю диалога Cerebras user_id=%s", user_id)

    if suggested_to_record:
        try:
            await db.record_cerebras_suggested_events(user_id, suggested_to_record)
        except Exception:
            logger.exception("Не удалось сохранить показанные мероприятия user_id=%s", user_id)

    await _send_plain_reply(message, reply)
    logger.debug(
        "Cerebras ответ user_id=%s city=%s interests=%s",
        user_id,
        city,
        [EVENT_CATEGORIES.get(x, x) for x in interests],
    )
