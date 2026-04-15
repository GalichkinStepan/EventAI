"""Проверка имён часовых поясов IANA (для городов в БД)."""

from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def validate_iana_timezone(name: str) -> str:
    """
    Проверяет, что строка — известный часовой пояс IANA (например Europe/Moscow).
    Возвращает строку без лишних пробелов по краям.
    """
    n = (name or "").strip()
    if not n:
        raise ValueError("Часовой пояс не может быть пустым.")
    try:
        ZoneInfo(n)
    except ZoneInfoNotFoundError as e:
        raise ValueError(
            f"Неизвестный часовой пояс «{n}». Укажите IANA, например: Europe/Moscow, Asia/Novosibirsk."
        ) from e
    return n
