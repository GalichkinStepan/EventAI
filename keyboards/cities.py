from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


def cities_keyboard(
    cities: list[dict],
    *,
    page: int = 0,
    per_page: int = 8,
) -> InlineKeyboardBuilder:
    """Кнопки городов + пагинация (callback: citypick:<id>, citypage:<n>)."""
    builder = InlineKeyboardBuilder()
    if not cities:
        return builder

    total_pages = max(1, (len(cities) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    chunk = cities[start : start + per_page]

    for row in chunk:
        builder.row(
            InlineKeyboardButton(
                text=row["name"],
                callback_data=f"citypick:{row['id']}",
            )
        )

    nav: list[InlineKeyboardButton] = []
    if total_pages > 1:
        if page > 0:
            nav.append(
                InlineKeyboardButton(text="« Назад", callback_data=f"citypage:{page - 1}")
            )
        if page < total_pages - 1:
            nav.append(
                InlineKeyboardButton(text="Вперёд »", callback_data=f"citypage:{page + 1}")
            )
        if nav:
            builder.row(*nav)

    return builder
