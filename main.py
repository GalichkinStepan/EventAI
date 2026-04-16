from __future__ import annotations

import asyncio
import logging
import sys
from logging.handlers import RotatingFileHandler

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

from config import Settings, load_settings
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
        while True:
            await asyncio.sleep(interval_sec)
            try:
                n = await sync_social_events_for_all_links(db, cerebras_service, settings)
                log.info("Мероприятия (VK/Telegram + Cerebras): сохранено записей: %d", n)
            except Exception:
                log.exception("Ошибка синхронизации мероприятий из соцсетей")

    if settings.use_webhook:
        assert settings.webhook_url is not None
        await _run_webhook(
            bot=bot,
            dp=dp,
            settings=settings,
            cerebras_service=cerebras_service,
            db=db,
            social_events_background=social_events_background,
            log=log,
        )
    else:
        await _run_polling(
            bot=bot,
            dp=dp,
            cerebras_service=cerebras_service,
            db=db,
            social_events_background=social_events_background,
            log=log,
        )


async def _run_polling(
    *,
    bot: Bot,
    dp: Dispatcher,
    cerebras_service: CerebrasService | None,
    db: Database,
    social_events_background,
    log: logging.Logger,
) -> None:
    events_task = asyncio.create_task(social_events_background())
    log.info("Режим polling (для webhook задайте WEBHOOK_URL в .env)")
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


async def _run_webhook(
    *,
    bot: Bot,
    dp: Dispatcher,
    settings: Settings,
    cerebras_service: CerebrasService | None,
    db: Database,
    social_events_background,
    log: logging.Logger,
) -> None:
    @dp.startup()
    async def _set_webhook(bot: Bot) -> None:
        assert settings.webhook_url is not None
        await bot.set_webhook(
            url=settings.webhook_url,
            secret_token=settings.webhook_secret,
            drop_pending_updates=True,
            allowed_updates=dp.resolve_used_update_types(),
        )
        log.info("Webhook зарегистрирован в Telegram: %s", settings.webhook_url)

    @dp.shutdown()
    async def _delete_webhook(bot: Bot) -> None:
        await bot.delete_webhook(drop_pending_updates=False)

    app = web.Application()

    async def health(_request: web.Request) -> web.Response:
        return web.Response(text="ok")

    app.router.add_get("/", health)

    async def start_events(aio_app: web.Application) -> None:
        aio_app["events_task"] = asyncio.create_task(social_events_background())

    app.on_startup.append(start_events)

    webhook_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=settings.webhook_secret,
    )
    webhook_handler.register(app, path=settings.webhook_path)

    setup_application(app, dp, bot=bot)

    async def stop_events(aio_app: web.Application) -> None:
        t = aio_app.get("events_task")
        if t:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass

    app.on_shutdown.append(stop_events)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", settings.webhook_port)
    await site.start()
    log.info(
        "Webhook: HTTP на 0.0.0.0:%s, путь POST %s",
        settings.webhook_port,
        settings.webhook_path,
    )
    try:
        halt = asyncio.Event()
        await halt.wait()
    finally:
        await runner.cleanup()
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
