"""Сообщения после завершения регистрации: текст пользователя → промпт для Cerebras."""

from __future__ import annotations

import logging

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
from states.user_preferences import ProfileSetup

logger = logging.getLogger(__name__)

# Лимит Telegram на длину одного сообщения
_MAX_MESSAGE_LEN = 4096

router = Router(name="chat")


def _split_reply(text: str) -> list[str]:
    """Разбивает текст на части ≤4096 символов (Unicode-safe)."""
    if len(text) <= _MAX_MESSAGE_LEN:
        return [text]
    return [text[i : i + _MAX_MESSAGE_LEN] for i in range(0, len(text), _MAX_MESSAGE_LEN)]


async def _send_plain_reply(message: Message, text: str) -> None:
    """Ответ без parse_mode: произвольный текст модели не должен ломать Markdown бота."""
    for part in _split_reply(text):
        await message.answer(part, parse_mode=None)


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
    upcoming: list[dict] = []
    city_tz: str | None = None
    cid = row.get("city_id")
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
        upcoming = await db.list_upcoming_events_for_city(
            cid_int,
            timezone=city_tz,
        )

    messages = build_chat_messages(
        raw,
        city=city,
        interests=interests,
        aggregator_links=agg or None,
        upcoming_events=upcoming or None,
        city_timezone=city_tz,
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

    await _send_plain_reply(message, reply)
    logger.debug(
        "Cerebras ответ user_id=%s city=%s interests=%s",
        user_id,
        city,
        [EVENT_CATEGORIES.get(x, x) for x in interests],
    )
