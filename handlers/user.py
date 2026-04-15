import logging
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from database.db import Database
from keyboards.categories import EVENT_CATEGORIES, categories_keyboard
from keyboards.cities import cities_keyboard
from states.user_preferences import ProfileSetup

logger = logging.getLogger(__name__)

router = Router(name="user")


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    state: FSMContext,
    db: Database,
) -> Any:
    if not message.from_user:
        logger.warning("/start без from_user")
        return

    user = message.from_user
    await db.upsert_user(user.id, user.username)
    logger.info("Пользователь зарегистрирован/обновлён: id=%s username=%s", user.id, user.username)

    cities = await db.list_cities()
    if not cities:
        await message.answer(
            "Регистрация временно недоступна: **список городов пуст**. "
            "Администратор ещё не добавил города. Попробуйте позже.\n\n"
            "Отмена: /cancel",
            parse_mode="Markdown",
        )
        return

    await state.set_state(ProfileSetup.city)
    await state.update_data(city_list_page=0)

    total_pages = max(1, (len(cities) + 7) // 8)
    extra = f"\n\nСтраница **1** из **{total_pages}**" if total_pages > 1 else ""

    await message.answer(
        "Привет! Выберите ваш **город** из списка — так мы сможем подбирать мероприятия и ссылки рядом с вами."
        f"{extra}\n\n"
        "Отмена: /cancel",
        reply_markup=cities_keyboard(cities, page=0).as_markup(),
        parse_mode="Markdown",
    )


@router.message(Command("id"))
async def cmd_id(message: Message) -> None:
    if not message.from_user:
        await message.answer("Не удалось определить пользователя.")
        return
    u = message.from_user
    uname = f"@{u.username}" if u.username else "не указан"
    await message.answer(
        f"Ваш **Telegram ID**: `{u.id}`\n**Username**: {uname}",
        parse_mode="Markdown",
    )


@router.message(Command("aggregators"))
async def cmd_aggregators(message: Message, db: Database) -> None:
    if not message.from_user:
        return
    row = await db.get_user(message.from_user.id)
    if not row or not row.get("city_id"):
        await message.answer(
            "Сначала завершите настройку профиля: /start и выбор города и интересов."
        )
        return

    links = await db.list_aggregators_for_city(int(row["city_id"]))
    if not links:
        await message.answer(
            "Для вашего города пока нет сохранённых ссылок на агрегаторы мероприятий."
        )
        return

    lines = [f"• {t['title']}\n  {t['url']}" for t in links]
    await message.answer(
        "Сайты агрегаторов для вашего города:\n\n" + "\n\n".join(lines),
        disable_web_page_preview=True,
    )


@router.message(Command("cancel"), StateFilter(ProfileSetup))
async def cmd_cancel_fsm(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Настройка отменена. Снова: /start")


@router.callback_query(StateFilter(ProfileSetup.city), F.data.startswith("citypage:"))
async def process_city_page(
    callback: CallbackQuery,
    state: FSMContext,
    db: Database,
) -> None:
    if not callback.message:
        await callback.answer()
        return

    parts = (callback.data or "").split(":")
    page = int(parts[1]) if len(parts) > 1 else 0

    cities = await db.list_cities()
    if not cities:
        await callback.answer("Список городов пуст", show_alert=True)
        return

    total_pages = max(1, (len(cities) + 7) // 8)
    page = max(0, min(page, total_pages - 1))
    await state.update_data(city_list_page=page)

    extra = f"\n\nСтраница **{page + 1}** из **{total_pages}**" if total_pages > 1 else ""
    try:
        await callback.message.edit_text(
            "Привет! Выберите ваш **город** из списка — так мы сможем подбирать мероприятия и ссылки рядом с вами."
            f"{extra}\n\n"
            "Отмена: /cancel",
            reply_markup=cities_keyboard(cities, page=page).as_markup(),
            parse_mode="Markdown",
        )
    except Exception:
        logger.exception("Не удалось обновить сообщение с городами")
        await callback.message.edit_reply_markup(reply_markup=cities_keyboard(cities, page=page).as_markup())
    await callback.answer()


@router.callback_query(StateFilter(ProfileSetup.city), F.data.startswith("citypick:"))
async def process_city_pick(
    callback: CallbackQuery,
    state: FSMContext,
    db: Database,
) -> None:
    if not callback.from_user or not callback.message:
        await callback.answer()
        return

    raw_id = (callback.data or "").split(":")[1] if ":" in (callback.data or "") else ""
    if not raw_id.isdigit():
        await callback.answer()
        return

    city_id = int(raw_id)
    city_row = await db.get_city(city_id)
    if not city_row:
        await callback.answer("Город не найден", show_alert=True)
        return

    city_name = city_row["name"]
    await state.update_data(city_id=city_id, city_name=city_name, selected_categories=[])
    await state.set_state(ProfileSetup.categories)

    kb = categories_keyboard(set()).as_markup()
    try:
        await callback.message.edit_text(
            f"Город: **{city_name}**\n\n"
            "Выберите **категории мероприятий** (можно несколько). "
            "Нажмите на категорию, чтобы отметить, затем «Готово».\n\n"
            "Отмена: /cancel",
            reply_markup=kb,
            parse_mode="Markdown",
        )
    except Exception:
        logger.exception("Не удалось отредактировать сообщение после выбора города")
        await callback.message.answer(
            f"Город: **{city_name}**\n\n"
            "Выберите **категории мероприятий** (можно несколько). "
            "Нажмите «Готово», когда закончите.",
            reply_markup=kb,
            parse_mode="Markdown",
        )
    await callback.answer()


@router.message(StateFilter(ProfileSetup.city))
async def city_need_inline(message: Message) -> None:
    await message.answer("Выберите город кнопками выше или нажмите /cancel.")


@router.callback_query(StateFilter(ProfileSetup.categories), F.data.startswith("cat:"))
async def process_category_toggle(
    callback: CallbackQuery,
    state: FSMContext,
    db: Database,
) -> None:
    if not callback.from_user or not callback.message:
        await callback.answer()
        return

    parts = (callback.data or "").split(":", maxsplit=1)
    action = parts[1] if len(parts) > 1 else ""

    data = await state.get_data()
    selected: set[str] = set(data.get("selected_categories") or [])

    if action == "done":
        city_id = data.get("city_id")
        city_name = data.get("city_name") or ""
        categories = sorted(selected)
        if not categories:
            await callback.answer("Выберите хотя бы одну категорию", show_alert=True)
            return
        if city_id is None:
            await callback.answer("Ошибка: город не выбран", show_alert=True)
            return

        user_id = callback.from_user.id
        try:
            await db.update_user_city_id(user_id, int(city_id))
            await db.update_interests(user_id, categories)
        except Exception:
            logger.exception("Не удалось сохранить профиль user_id=%s", user_id)
            await callback.answer("Ошибка сохранения. Попробуйте позже.", show_alert=True)
            return

        await state.clear()
        labels = [EVENT_CATEGORIES.get(c, c) for c in categories]
        welcome = (
            "Настройки сохранены.\n\n"
            f"Город: {city_name}\n"
            f"Интересы: {', '.join(labels)}\n\n"
            "Добро пожаловать! Дальше можно просто писать сообщения в этот чат — "
            "спрашивайте о подборе мероприятий в обычном разговорном виде: что посмотреть на выходных, "
            "куда сходить по вашим интересам, что интересного в городе и т.п. "
            "Бот ответит в формате диалога, с учётом вашего города и выбранных категорий.\n\n"
            "Ссылки на сайты агрегаторов по городу: /aggregators"
        )
        await callback.message.edit_text(welcome)
        await callback.answer()
        logger.info(
            "Профиль сохранён user_id=%s city_id=%s categories=%s",
            user_id,
            city_id,
            categories,
        )
        return

    if action not in EVENT_CATEGORIES:
        await callback.answer()
        return

    if action in selected:
        selected.discard(action)
    else:
        selected.add(action)

    await state.update_data(selected_categories=list(selected))
    kb = categories_keyboard(selected).as_markup()
    try:
        await callback.message.edit_reply_markup(reply_markup=kb)
    except Exception:
        logger.exception("Не удалось обновить клавиатуру")
    await callback.answer()


@router.message(StateFilter(ProfileSetup.categories))
async def categories_need_inline(message: Message) -> None:
    await message.answer("Используйте кнопки под предыдущим сообщением или /cancel.")
