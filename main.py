from __future__ import annotations

import asyncio
import logging
import sys
from logging.handlers import RotatingFileHandler

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from config import load_settings
from database.db import Database
from handlers import setup_routers
from middlewares import AdminMiddleware, CerebrasMiddleware, DatabaseMiddleware, LoggingMiddleware
from services.cerebras import CerebrasService
from services.events_ingest import sync_social_events_for_all_links


def setup_logging() -> None:
    log_format = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(log_format))

    file_handler = RotatingFileHandler(
        "bot.log",
        maxBytes=2_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(log_format))

    root.handlers.clear()
    root.addHandler(console)
    root.addHandler(file_handler)

    logging.getLogger("aiogram").setLevel(logging.INFO)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)


async def main() -> None:
    setup_logging()
    log = logging.getLogger(__name__)

    settings = load_settings()
    if not settings.admin_telegram_ids:
        log.warning(
            "Администраторы не заданы: укажите в .env ADMIN_TELEGRAM_IDS "
            "(или TELEGRAM_ID / ADMIN_ID) — числовой id из @userinfobot."
        )
    else:
        log.info("Администраторов в конфигурации: %d", len(settings.admin_telegram_ids))

    db = Database(settings.database_url)
    await db.connect()

    cerebras_service: CerebrasService | None = None
    if settings.cerebras_api_key:
        cerebras_service = CerebrasService(settings.cerebras_api_key, settings.cerebras_model)
        log.info("Cerebras включён, модель %s", settings.cerebras_model)
    else:
        log.warning("CEREBRAS_API_KEY не задан — ответы ИИ отключены до настройки .env")

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN),
    )
    dp = Dispatcher(storage=MemoryStorage())

    dp.update.middleware(LoggingMiddleware())
    dp.update.middleware(DatabaseMiddleware(db))
    dp.update.middleware(AdminMiddleware(settings.admin_telegram_ids))
    dp.update.middleware(CerebrasMiddleware(cerebras_service))

    dp.include_router(setup_routers())

    async def social_events_background() -> None:
        if not settings.events_sync_enabled:
            log.info("Синхронизация мероприятий отключена (EVENTS_SYNC_ENABLED)")
            return
        if cerebras_service is None:
            log.warning(
                "CEREBRAS_API_KEY не задан — отбор мероприятий из постов VK/Telegram недоступен"
            )
            return
        interval_sec = max(300, settings.events_sync_interval_hours * 3600)
        log.info(
            "Фоновая синхронизация мероприятий: EVENTS_SYNC_INTERVAL_HOURS=%s "
            "(пауза между циклами %s с, ~%.1f ч); первый автоматический запуск — после первой паузы. "
            "Ручной запуск: /sync_events",
            settings.events_sync_interval_hours,
            interval_sec,
            interval_sec / 3600,
        )
        # Парсинг мероприятий при старте бота не выполняем — только по расписанию
        # (после первой паузы) или вручную: команда /sync_events у администратора.
        while True:
            await asyncio.sleep(interval_sec)
            try:
                n = await sync_social_events_for_all_links(db, cerebras_service, settings)
                log.info("Мероприятия (VK/Telegram + Cerebras): сохранено записей: %d", n)
            except Exception:
                log.exception("Ошибка синхронизации мероприятий из соцсетей")

    events_task = asyncio.create_task(social_events_background())

    log.info("Бот запущен")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        events_task.cancel()
        try:
            await events_task
        except asyncio.CancelledError:
            pass
        if cerebras_service is not None:
            await cerebras_service.close()
        await db.close()
        await bot.session.close()
        log.info("Бот остановлен")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.getLogger(__name__).info("Остановка по запросу пользователя")
