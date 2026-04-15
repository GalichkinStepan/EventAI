import logging

from aiogram import Router
from aiogram.types import ErrorEvent

logger = logging.getLogger(__name__)

router = Router(name="errors")


@router.errors()
async def global_error_handler(event: ErrorEvent) -> None:
    logger.exception(
        "Необработанное исключение в обработчике: %s",
        event.exception,
        exc_info=event.exception,
    )
    try:
        if event.update.message:
            await event.update.message.answer(
                "Произошла внутренняя ошибка. Попробуйте позже или отправьте /start."
            )
        elif event.update.callback_query and event.update.callback_query.message:
            await event.update.callback_query.message.answer(
                "Произошла внутренняя ошибка. Попробуйте позже или отправьте /start."
            )
    except Exception:
        logger.exception("Не удалось уведомить пользователя об ошибке")
