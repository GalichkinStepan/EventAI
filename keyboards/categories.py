from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

# id -> подпись для пользователя
EVENT_CATEGORIES: dict[str, str] = {
    "music": "Музыка и концерты",
    "sport": "Спорт",
    "theater": "Театр и кино",
    "education": "Образование и лекции",
    "networking": "Нетворкинг",
    "festivals": "Фестивали",
}


def categories_keyboard(selected: set[str]) -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    for cat_id, title in EVENT_CATEGORIES.items():
        mark = "✓ " if cat_id in selected else ""
        builder.row(
            InlineKeyboardButton(
                text=f"{mark}{title}",
                callback_data=f"cat:{cat_id}",
            )
        )
    builder.row(
        InlineKeyboardButton(text="Готово", callback_data="cat:done"),
    )
    return builder
