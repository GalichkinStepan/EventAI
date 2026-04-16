"""Промпт для отбора анонсов мероприятий и структурирования данных (Cerebras)."""

from __future__ import annotations

import json
from typing import Any

from services.social.models import SocialPost

EVENT_EXTRACTION_SYSTEM = """Ты фильтруешь посты из VK и Telegram. На входе — JSON-массив постов с полями ref, url, published_at, text.
Оставь только те посты, которые анонсируют мероприятие (концерт, спектакль, выставка, кино, фестиваль, лекция, стендап, экскурсия, встреча с автором, мастер-класс и т.п.).
Реклама без события, новости без анонса, розыгрыши без даты события — отбрось.

В ответ НЕ включай пост, если из текста нельзя однозначно извлечь ОБА условия: (1) дату и время начала мероприятия; (2) место проведения — хотя бы название площадки ИЛИ адрес (город+улица и т.п.). Без даты или без места такой ref в массив не попадает.

Дубликаты мероприятий убери: если несколько постов про одно и то же событие (одинаковое название/дата/площадка, репосты, напоминания, серия анонсов одного концерта), в массиве должен остаться один элемент — с самым полным описанием и точной датой; остальные такие посты не включай (один ref на одно уникальное мероприятие в ответе).

Верни СТРОГО один JSON-массив (без markdown, без пояснений), каждый элемент:
{
  "ref": "тот же ref из входа",
  "title": "краткое название мероприятия",
  "description_text": "суть на русском, не более 120 символов (суть, дата/время, место, цена — умести в лимит)",
  "starts_at": "ISO8601 с таймзоной (обязательно, не null)",
  "venue_name": "название площадки (клуб, театр, зал) или null, если место задано только адресом",
  "street_address": "адрес: город, улица, дом (как в тексте поста) или null, если место задано только названием площадки",
  "event_kind": "тип (концерт, спектакль и т.д.) или null"
}

Для каждого элемента массива должно выполняться: starts_at не null, и заполнено хотя бы одно из полей venue_name или street_address (непустая строка).

Если подходящих постов нет — верни []."""


def build_event_extraction_messages(posts: list[SocialPost]) -> list[dict[str, Any]]:
    payload = [
        {
            "ref": p.ref,
            "url": p.url,
            "published_at": p.published_at.isoformat(),
            "text": p.text,
        }
        for p in posts
    ]
    user = "Входные посты (JSON):\n" + json.dumps(payload, ensure_ascii=False)
    return [
        {"role": "system", "content": EVENT_EXTRACTION_SYSTEM},
        {"role": "user", "content": user},
    ]


def parse_json_array_from_llm(text: str) -> list[dict[str, Any]]:
    t = text.strip()
    if t.startswith("```"):
        lines = t.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    data = json.loads(t)
    if not isinstance(data, list):
        raise ValueError("Ожидался JSON-массив")
    out: list[dict[str, Any]] = []
    for x in data:
        if isinstance(x, dict):
            out.append(x)
    return out
