import logging
import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

logger = logging.getLogger(__name__)


class LoggingMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user_id = None
        if isinstance(event, Message) and event.from_user:
            user_id = event.from_user.id
        elif isinstance(event, CallbackQuery) and event.from_user:
            user_id = event.from_user.id

        start = time.perf_counter()
        try:
            result = await handler(event, data)
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.debug(
                "Обработано событие user_id=%s за %.1f мс",
                user_id,
                elapsed_ms,
            )
            return result
        except Exception:
            logger.exception("Ошибка в обработчике user_id=%s", user_id)
            raise
