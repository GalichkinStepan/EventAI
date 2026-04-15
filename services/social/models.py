"""Сырые посты из VK и Telegram (публичный канал t.me/s/...)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class SocialPost:
    """Пост за окно синхронизации (последние N дней)."""

    ref: str
    """Стабильный идентификатор для LLM: vk:owner_post или tg:channel/msgid."""

    url: str
    text: str
    published_at: datetime
    source: str  # "vk" | "telegram"
