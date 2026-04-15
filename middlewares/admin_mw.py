from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.dispatcher.middlewares.user_context import UserContextMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, Update


class AdminMiddleware(BaseMiddleware):
    """Прокидывает is_admin: bool (user id из ADMIN_TELEGRAM_IDS или TELEGRAM_ID и др.)."""

    def __init__(self, admin_ids: frozenset[int]) -> None:
        self._admin_ids = admin_ids

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        # dp.update.middleware вызывается с event=Update, а не с Message — иначе from_user не находится.
        user = None
        if isinstance(event, Update):
            user = UserContextMiddleware.resolve_event_context(event).user
        elif isinstance(event, Message) and event.from_user:
            user = event.from_user
        elif isinstance(event, CallbackQuery) and event.from_user:
            user = event.from_user

        data["is_admin"] = user.id in self._admin_ids if user else False
        return await handler(event, data)
