from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware

from services.cerebras import CerebrasService


class CerebrasMiddleware(BaseMiddleware):
    """Прокидывает CerebrasService в data хендлеров (или None, если ключ не задан)."""

    def __init__(self, service: CerebrasService | None) -> None:
        self._service = service

    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: Dict[str, Any],
    ) -> Any:
        data["cerebras"] = self._service
        return await handler(event, data)
