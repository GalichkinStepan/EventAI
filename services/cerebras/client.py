from __future__ import annotations

import logging
from typing import Any

from openai import APIStatusError, AsyncOpenAI

logger = logging.getLogger(__name__)

# OpenAI-совместимый endpoint: https://inference-docs.cerebras.ai/resources/openai
CEREBRAS_BASE_URL = "https://api.cerebras.ai/v1"


class CerebrasPaymentRequiredError(Exception):
    """HTTP 402 — недостаточно средств / оплата на аккаунте Cerebras."""


class CerebrasService:
    """Chat Completions через Cerebras Inference API (совместим с OpenAI SDK)."""

    def __init__(self, api_key: str, model: str) -> None:
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=CEREBRAS_BASE_URL,
        )
        self._model = model

    async def complete(self, messages: list[dict[str, Any]], *, temperature: float = 0.7) -> str:
        logger.info(
            "Cerebras: POST chat/completions model=%s temperature=%s messages=%d",
            self._model,
            temperature,
            len(messages),
        )
        try:
            resp = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=temperature,
            )
            choice = resp.choices[0].message
            return (choice.content or "").strip()
        except APIStatusError as e:
            if e.status_code == 402:
                logger.warning(
                    "Cerebras API: требуется оплата или недостаточно средств (402). "
                    "Проверьте биллинг: https://cloud.cerebras.ai/"
                )
                raise CerebrasPaymentRequiredError("Payment Required") from e
            logger.exception("Ошибка Cerebras API: %s", e)
            raise
        except Exception:
            logger.exception("Ошибка Cerebras API")
            raise

    async def extract_event_rows_from_posts_json(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.2,
    ) -> str:
        """Ответ модели — JSON-массив объектов мероприятий."""
        return await self.complete(messages, temperature=temperature)

    async def close(self) -> None:
        await self._client.close()
