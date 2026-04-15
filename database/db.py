from __future__ import annotations

import json
import logging
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS cities (
        id SERIAL PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        sort_order INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ DEFAULT NOW()
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS aggregator_links (
        id SERIAL PRIMARY KEY,
        city_id INTEGER NOT NULL REFERENCES cities(id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        url TEXT NOT NULL,
        sort_order INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ DEFAULT NOW()
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS users (
        user_id BIGINT PRIMARY KEY NOT NULL,
        username TEXT,
        city TEXT,
        interests TEXT
    );
    """,
    """
    ALTER TABLE users ADD COLUMN IF NOT EXISTS city_id INTEGER REFERENCES cities(id);
    """,
    """
    CREATE TABLE IF NOT EXISTS events (
        id BIGSERIAL PRIMARY KEY,
        city_id INTEGER REFERENCES cities(id) ON DELETE SET NULL,
        aggregator_link_id INTEGER REFERENCES aggregator_links(id) ON DELETE SET NULL,
        source TEXT NOT NULL DEFAULT 'social',
        external_key TEXT NOT NULL,
        title TEXT NOT NULL,
        description_text TEXT NOT NULL,
        event_kind TEXT,
        starts_at TIMESTAMPTZ,
        ends_at TIMESTAMPTZ,
        venue_name TEXT,
        venue_url TEXT,
        street_address TEXT,
        price_amount NUMERIC(12, 2),
        currency TEXT,
        source_url TEXT NOT NULL,
        image_url TEXT,
        raw_json JSONB,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        updated_at TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE (source, external_key)
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_events_city_starts ON events (city_id, starts_at);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_events_source ON events (source);
    """,
    """
    ALTER TABLE events ADD COLUMN IF NOT EXISTS aggregator_link_id INTEGER
        REFERENCES aggregator_links(id) ON DELETE SET NULL;
    """,
    """
    ALTER TABLE events ALTER COLUMN source SET DEFAULT 'social';
    """,
    """
    ALTER TABLE cities ADD COLUMN IF NOT EXISTS timezone TEXT NOT NULL DEFAULT 'Europe/Moscow';
    """,
]


class Database:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        self._pool = await asyncpg.create_pool(
            self._dsn,
            min_size=1,
            max_size=10,
        )
        assert self._pool is not None
        async with self._pool.acquire() as conn:
            for stmt in SCHEMA_STATEMENTS:
                await conn.execute(stmt)
        logger.info("PostgreSQL: пул подключён, схема проверена")

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
        logger.info("Соединение с БД закрыто")

    def _require_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("База данных не подключена")
        return self._pool

    async def upsert_user(self, user_id: int, username: str | None) -> None:
        pool = self._require_pool()
        await pool.execute(
            """
            INSERT INTO users (user_id, username, city, interests, city_id)
            VALUES ($1, $2, NULL, NULL, NULL)
            ON CONFLICT (user_id) DO UPDATE SET username = EXCLUDED.username
            """,
            user_id,
            username,
        )

    async def update_user_city_id(self, user_id: int, city_id: int) -> None:
        pool = self._require_pool()
        await pool.execute(
            "UPDATE users SET city_id = $1, city = NULL WHERE user_id = $2",
            city_id,
            user_id,
        )

    async def update_interests(self, user_id: int, interests: list[str]) -> None:
        pool = self._require_pool()
        payload = json.dumps(interests, ensure_ascii=False)
        await pool.execute(
            "UPDATE users SET interests = $1 WHERE user_id = $2",
            payload,
            user_id,
        )

    async def get_user(self, user_id: int) -> dict[str, Any] | None:
        pool = self._require_pool()
        row = await pool.fetchrow(
            """
            SELECT u.user_id, u.username, u.city_id,
                   COALESCE(c.name, u.city, '') AS city,
                   c.timezone AS city_timezone,
                   u.interests
            FROM users u
            LEFT JOIN cities c ON c.id = u.city_id
            WHERE u.user_id = $1
            """,
            user_id,
        )
        if row is None:
            return None
        data = dict(row)
        if data.get("interests"):
            try:
                data["interests"] = json.loads(data["interests"])
            except json.JSONDecodeError:
                logger.warning("Некорректный JSON interests у user_id=%s", user_id)
                data["interests"] = []
        else:
            data["interests"] = None
        return data

    async def is_profile_complete(self, user_id: int) -> bool:
        user = await self.get_user(user_id)
        if not user:
            return False
        has_city = user.get("city_id") is not None or (
            user.get("city") and str(user["city"]).strip()
        )
        interests = user.get("interests")
        if not has_city:
            return False
        if not interests or not isinstance(interests, list) or len(interests) < 1:
            return False
        return True

    async def list_cities(self) -> list[dict[str, Any]]:
        pool = self._require_pool()
        rows = await pool.fetch(
            "SELECT id, name, sort_order, timezone FROM cities ORDER BY sort_order ASC, name ASC"
        )
        return [dict(r) for r in rows]

    async def get_city(self, city_id: int) -> dict[str, Any] | None:
        pool = self._require_pool()
        row = await pool.fetchrow(
            "SELECT id, name, sort_order, timezone FROM cities WHERE id = $1",
            city_id,
        )
        return dict(row) if row else None

    async def add_city(
        self, name: str, timezone: str, sort_order: int = 0
    ) -> int:
        pool = self._require_pool()
        name_clean = name.strip()
        tz_clean = timezone.strip()
        try:
            cid = await pool.fetchval(
                """
                INSERT INTO cities (name, sort_order, timezone)
                VALUES ($1, $2, $3)
                RETURNING id
                """,
                name_clean,
                sort_order,
                tz_clean,
            )
            assert cid is not None
            return int(cid)
        except asyncpg.UniqueViolationError:
            row = await pool.fetchrow("SELECT id FROM cities WHERE name = $1", name_clean)
            if row:
                return int(row["id"])
            raise

    async def update_city_timezone(self, city_id: int, timezone: str) -> bool:
        pool = self._require_pool()
        result = await pool.execute(
            "UPDATE cities SET timezone = $2 WHERE id = $1",
            city_id,
            timezone.strip(),
        )
        return result != "UPDATE 0"

    async def delete_city(self, city_id: int) -> bool:
        pool = self._require_pool()
        result = await pool.execute("DELETE FROM cities WHERE id = $1", city_id)
        return result != "DELETE 0"

    async def add_aggregator_link(
        self, city_id: int, title: str, url: str, sort_order: int = 0
    ) -> int:
        pool = self._require_pool()
        lid = await pool.fetchval(
            """
            INSERT INTO aggregator_links (city_id, title, url, sort_order)
            VALUES ($1, $2, $3, $4)
            RETURNING id
            """,
            city_id,
            title.strip(),
            url.strip(),
            sort_order,
        )
        assert lid is not None
        return int(lid)

    async def delete_aggregator_link(self, link_id: int) -> bool:
        pool = self._require_pool()
        result = await pool.execute("DELETE FROM aggregator_links WHERE id = $1", link_id)
        return result != "DELETE 0"

    async def list_aggregators_for_city(self, city_id: int) -> list[dict[str, Any]]:
        pool = self._require_pool()
        rows = await pool.fetch(
            """
            SELECT id, city_id, title, url, sort_order
            FROM aggregator_links
            WHERE city_id = $1
            ORDER BY sort_order ASC, id ASC
            """,
            city_id,
        )
        return [dict(r) for r in rows]

    async def list_all_aggregator_links(self) -> list[dict[str, Any]]:
        pool = self._require_pool()
        rows = await pool.fetch(
            """
            SELECT id, city_id, title, url, sort_order
            FROM aggregator_links
            ORDER BY city_id ASC, sort_order ASC, id ASC
            """
        )
        return [dict(r) for r in rows]

    async def get_city_id_by_name(self, name: str) -> int | None:
        pool = self._require_pool()
        row = await pool.fetchrow(
            "SELECT id FROM cities WHERE LOWER(name) = LOWER($1)",
            name.strip(),
        )
        return int(row["id"]) if row else None

    async def list_upcoming_events_for_city(
        self,
        city_id: int,
        *,
        timezone: str,
        limit: int = 80,
    ) -> list[dict[str, Any]]:
        """
        Мероприятия города: дата начала в календаре города (IANA) не раньше «сегодня» в том же поясе.
        """
        pool = self._require_pool()
        rows = await pool.fetch(
            """
            SELECT id, title, description_text, event_kind, starts_at,
                   venue_name, street_address, source_url
            FROM events
            WHERE city_id = $1
              AND starts_at IS NOT NULL
              AND (starts_at AT TIME ZONE $3)::date
                  >= (CURRENT_TIMESTAMP AT TIME ZONE $3)::date
            ORDER BY starts_at ASC NULLS LAST
            LIMIT $2
            """,
            city_id,
            limit,
            timezone.strip(),
        )
        return [dict(r) for r in rows]

    async def upsert_event(
        self,
        *,
        city_id: int | None,
        aggregator_link_id: int | None,
        source: str,
        external_key: str,
        title: str,
        description_text: str,
        event_kind: str | None,
        starts_at: Any,
        ends_at: Any,
        venue_name: str | None,
        venue_url: str | None,
        street_address: str | None,
        price_amount: Any,
        currency: str | None,
        source_url: str,
        image_url: str | None,
        raw_json: dict[str, Any] | None,
    ) -> int:
        pool = self._require_pool()
        # asyncpg ожидает JSONB как str (или спец-тип), не как dict
        raw_json_db: str | None = (
            json.dumps(raw_json, ensure_ascii=False) if raw_json is not None else None
        )
        eid = await pool.fetchval(
            """
            INSERT INTO events (
                city_id, aggregator_link_id, source, external_key, title, description_text, event_kind,
                starts_at, ends_at, venue_name, venue_url, street_address,
                price_amount, currency, source_url, image_url, raw_json, updated_at
            )
            VALUES (
                $1, $2, $3, $4, $5, $6, $7,
                $8, $9, $10, $11, $12,
                $13, $14, $15, $16, $17::jsonb, NOW()
            )
            ON CONFLICT (source, external_key) DO UPDATE SET
                city_id = EXCLUDED.city_id,
                aggregator_link_id = EXCLUDED.aggregator_link_id,
                title = EXCLUDED.title,
                description_text = EXCLUDED.description_text,
                event_kind = EXCLUDED.event_kind,
                starts_at = EXCLUDED.starts_at,
                ends_at = EXCLUDED.ends_at,
                venue_name = EXCLUDED.venue_name,
                venue_url = EXCLUDED.venue_url,
                street_address = EXCLUDED.street_address,
                price_amount = EXCLUDED.price_amount,
                currency = EXCLUDED.currency,
                source_url = EXCLUDED.source_url,
                image_url = EXCLUDED.image_url,
                raw_json = EXCLUDED.raw_json,
                updated_at = NOW()
            RETURNING id
            """,
            city_id,
            aggregator_link_id,
            source,
            external_key,
            title,
            description_text,
            event_kind,
            starts_at,
            ends_at,
            venue_name,
            venue_url,
            street_address,
            price_amount,
            currency,
            source_url,
            image_url,
            raw_json_db,
        )
        assert eid is not None
        return int(eid)
