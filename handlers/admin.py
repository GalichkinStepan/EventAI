"""Команды администратора: города и ссылки на агрегаторы."""

from __future__ import annotations

import logging
import re

import asyncpg
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from config import load_settings
from database.db import Database
from services.cerebras import CerebrasService
from services.events_ingest import sync_social_events_for_all_links
from timezone_utils import validate_iana_timezone

logger = logging.getLogger(__name__)

router = Router(name="admin")


@router.message(Command("admin"))
async def cmd_admin(message: Message, is_admin: bool) -> None:
    if not is_admin:
        await message.answer("Эта команда только для администратора бота.")
        return

    await message.answer(
        "**Панель администратора**\n\n"
        "/add\\_city `<IANA>` `<название города>` — добавить город с часовым поясом "
        "(например: `/add_city Europe/Moscow Москва`)\n"
        "/set\\_city\\_tz `<id>` `<IANA>` — сменить часовой пояс города\n"
        "/remove\\_city `<id>` — удалить город\n"
        "/cities — список городов (id, название, часовой пояс)\n"
        "/add\\_link `<id_города>` `<url>` `[название]` — VK-группа или Telegram-канал "
        "(посты за 2 дня → Cerebras → мероприятия в БД)\n"
        "/links\\_city `<id_города>` — список ссылок города\n"
        "/remove\\_link `<id_ссылки>` — удалить ссылку\n"
        "/sync\\_events — загрузить посты VK/Telegram и отфильтровать мероприятия (Cerebras)\n\n"
        "Подсказка: id города и ссылок смотрите в /cities и /links\\_city.",
        parse_mode="Markdown",
    )


@router.message(Command("sync_events"))
async def cmd_sync_events(
    message: Message,
    db: Database,
    is_admin: bool,
    cerebras: CerebrasService | None,
) -> None:
    if not is_admin:
        await message.answer("Доступ запрещён.")
        return
    if cerebras is None:
        await message.answer(
            "CEREBRAS_API_KEY не задан в .env — синхронизация мероприятий недоступна."
        )
        return
    settings = load_settings()
    if not settings.events_sync_enabled:
        await message.answer(
            "Синхронизация отключена (EVENTS_SYNC_ENABLED=0 в .env)."
        )
        return
    await message.answer("Запускаю загрузку и разбор мероприятий по ссылкам из БД…")
    try:
        n = await sync_social_events_for_all_links(db, cerebras, settings)
        await message.answer(f"Готово. Сохранено или обновлено записей мероприятий: **{n}**", parse_mode="Markdown")
    except Exception:
        logger.exception("sync_events")
        await message.answer("Ошибка синхронизации. Подробности в логе бота.")


@router.message(Command("add_city"))
async def cmd_add_city(message: Message, db: Database, is_admin: bool) -> None:
    if not is_admin:
        await message.answer("Доступ запрещён.")
        return

    text = (message.text or "").strip()
    m = re.match(r"^/add_city\s+(\S+)\s+(.+)$", text, re.DOTALL)
    if not m:
        await message.answer(
            "Формат: `/add_city <IANA> <название города>`\n"
            "Пример: `/add_city Asia/Novosibirsk Новосибирск`\n"
            "Список поясов: https://en.wikipedia.org/wiki/List_of_tz_database_time_zones",
            parse_mode="Markdown",
        )
        return

    tz_raw = m.group(1).strip()
    name = m.group(2).strip()
    if len(name) < 2:
        await message.answer("Название города слишком короткое.")
        return

    try:
        tz = validate_iana_timezone(tz_raw)
    except ValueError as e:
        await message.answer(str(e))
        return

    try:
        cid = await db.add_city(name, timezone=tz)
        await message.answer(
            f"Город добавлен: **{name}** (id={cid}), часовой пояс: `{tz}`",
            parse_mode="Markdown",
        )
    except Exception:
        logger.exception("add_city")
        await message.answer("Не удалось добавить город.")


@router.message(Command("set_city_tz"))
async def cmd_set_city_tz(message: Message, db: Database, is_admin: bool) -> None:
    if not is_admin:
        await message.answer("Доступ запрещён.")
        return

    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3 or not parts[1].isdigit():
        await message.answer(
            "Формат: `/set_city_tz 2 Europe/Moscow`",
            parse_mode="Markdown",
        )
        return

    city_id = int(parts[1])
    tz_raw = parts[2].strip()
    try:
        tz = validate_iana_timezone(tz_raw)
    except ValueError as e:
        await message.answer(str(e))
        return

    city = await db.get_city(city_id)
    if not city:
        await message.answer("Город с таким id не найден. Смотрите /cities")
        return

    try:
        ok = await db.update_city_timezone(city_id, tz)
    except Exception:
        logger.exception("set_city_tz")
        await message.answer("Не удалось обновить часовой пояс.")
        return

    if ok:
        await message.answer(
            f"Часовой пояс для **{city['name']}** (id={city_id}): `{tz}`",
            parse_mode="Markdown",
        )
    else:
        await message.answer("Город не найден.")


@router.message(Command("remove_city"))
async def cmd_remove_city(message: Message, db: Database, is_admin: bool) -> None:
    if not is_admin:
        await message.answer("Доступ запрещён.")
        return

    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Формат: `/remove_city 3`", parse_mode="Markdown")
        return

    city_id = int(parts[1])
    try:
        ok = await db.delete_city(city_id)
    except asyncpg.ForeignKeyViolationError:
        await message.answer(
            "Нельзя удалить город: к нему привязаны пользователи. "
            "Сначала смените город пользователям или удалите ссылки (/links_city)."
        )
        return
    except Exception:
        logger.exception("remove_city")
        await message.answer("Ошибка удаления.")
        return

    if ok:
        await message.answer(f"Город id={city_id} удалён.")
    else:
        await message.answer("Город с таким id не найден.")


@router.message(Command("cities"))
async def cmd_cities(message: Message, db: Database, is_admin: bool) -> None:
    if not is_admin:
        await message.answer("Доступ запрещён.")
        return

    cities = await db.list_cities()
    if not cities:
        await message.answer(
            "Список городов пуст. Добавьте: `/add_city Europe/Moscow Название`",
            parse_mode="Markdown",
        )
        return

    lines = [
        f"`{c['id']}` — {c['name']} — `{c.get('timezone', 'Europe/Moscow')}`"
        for c in cities
    ]
    await message.answer("**Города:**\n" + "\n".join(lines), parse_mode="Markdown")


@router.message(Command("add_link"))
async def cmd_add_link(message: Message, db: Database, is_admin: bool) -> None:
    if not is_admin:
        await message.answer("Доступ запрещён.")
        return

    text = (message.text or "").strip()
    m = re.match(
        r"/add_link\s+(\d+)\s+(https?://\S+)(?:\s+(.+))?$",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if not m:
        await message.answer(
            "Формат:\n`/add_link 2 https://kudago.com/msk КудаГо`",
            parse_mode="Markdown",
        )
        return

    city_id = int(m.group(1))
    url = m.group(2).rstrip(".,;)")
    title = (m.group(3) or "Агрегатор мероприятий").strip()

    city = await db.get_city(city_id)
    if not city:
        await message.answer("Город с таким id не найден. Смотрите /cities")
        return

    try:
        lid = await db.add_aggregator_link(city_id, title, url)
        await message.answer(
            f"Ссылка добавлена (id={lid}) для города **{city['name']}**:\n{title}\n{url}",
            parse_mode="Markdown",
        )
    except Exception:
        logger.exception("add_link")
        await message.answer("Не удалось сохранить ссылку.")


@router.message(Command("links_city"))
async def cmd_links_city(message: Message, db: Database, is_admin: bool) -> None:
    if not is_admin:
        await message.answer("Доступ запрещён.")
        return

    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Формат: `/links_city 2`", parse_mode="Markdown")
        return

    city_id = int(parts[1])
    city = await db.get_city(city_id)
    if not city:
        await message.answer("Город не найден.")
        return

    links = await db.list_aggregators_for_city(city_id)
    if not links:
        await message.answer(f"Для «{city['name']}» пока нет ссылок.")
        return

    lines = [f"`{L['id']}` — {L['title']}\n{L['url']}" for L in links]
    await message.answer(
        f"Ссылки — **{city['name']}**:\n\n" + "\n\n".join(lines),
        parse_mode="Markdown",
    )


@router.message(Command("remove_link"))
async def cmd_remove_link(message: Message, db: Database, is_admin: bool) -> None:
    if not is_admin:
        await message.answer("Доступ запрещён.")
        return

    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Формат: `/remove_link 5`", parse_mode="Markdown")
        return

    link_id = int(parts[1])
    ok = await db.delete_aggregator_link(link_id)
    if ok:
        await message.answer(f"Ссылка id={link_id} удалена.")
    else:
        await message.answer("Ссылка с таким id не найдена.")
