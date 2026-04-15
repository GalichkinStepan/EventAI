from aiogram import Router

from handlers import admin, chat, errors, user


def setup_routers() -> Router:
    root = Router(name="root")
    root.include_router(errors.router)
    root.include_router(admin.router)
    root.include_router(user.router)
    root.include_router(chat.router)
    return root
