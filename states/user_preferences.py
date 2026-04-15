from aiogram.fsm.state import State, StatesGroup


class ProfileSetup(StatesGroup):
    """Настройка города и категорий мероприятий."""

    city = State()
    categories = State()
