from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


@dataclass(frozen=True)
class CachedResponse:
    status_code: int
    headers: dict[str, str]
    body: dict
    fetched_at: datetime
    ttl_seconds: int

    def is_fresh(self, now: datetime | None = None) -> bool:
        now = now or utc_now()
        return now < (self.fetched_at + timedelta(seconds=self.ttl_seconds))


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS kv_store (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS http_cache (
          key TEXT PRIMARY KEY,
          status_code INTEGER NOT NULL,
          headers_json TEXT NOT NULL,
          body_json TEXT NOT NULL,
          fetched_at TEXT NOT NULL,
          ttl_seconds INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS webhook_subscriptions (
          id TEXT PRIMARY KEY,
          created_at TEXT NOT NULL,
          url TEXT NOT NULL,
          event_type TEXT NOT NULL,
          filters_json TEXT NOT NULL,
          secret TEXT NOT NULL,
          active INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS seen_events (
          source TEXT NOT NULL,
          external_id TEXT NOT NULL,
          first_seen_at TEXT NOT NULL,
          PRIMARY KEY (source, external_id)
        )
        """
    )
    conn.commit()


def kv_get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM kv_store WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def kv_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    now = utc_now().isoformat()
    conn.execute(
        """
        INSERT INTO kv_store(key, value, updated_at)
        VALUES(?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
        """,
        (key, value, now),
    )
    conn.commit()


def cache_get(conn: sqlite3.Connection, key: str) -> CachedResponse | None:
    row = conn.execute("SELECT * FROM http_cache WHERE key = ?", (key,)).fetchone()
    if not row:
        return None
    return CachedResponse(
        status_code=int(row["status_code"]),
        headers=json.loads(row["headers_json"]),
        body=json.loads(row["body_json"]),
        fetched_at=datetime.fromisoformat(row["fetched_at"]),
        ttl_seconds=int(row["ttl_seconds"]),
    )


def cache_set(conn: sqlite3.Connection, key: str, response: CachedResponse) -> None:
    conn.execute(
        """
        INSERT INTO http_cache(key, status_code, headers_json, body_json, fetched_at, ttl_seconds)
        VALUES(?, ?, ?, ?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
          status_code=excluded.status_code,
          headers_json=excluded.headers_json,
          body_json=excluded.body_json,
          fetched_at=excluded.fetched_at,
          ttl_seconds=excluded.ttl_seconds
        """,
        (
            key,
            response.status_code,
            json.dumps(response.headers, sort_keys=True),
            json.dumps(response.body, sort_keys=True),
            response.fetched_at.isoformat(),
            response.ttl_seconds,
        ),
    )
    conn.commit()


def seen_event_exists(conn: sqlite3.Connection, source: str, external_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM seen_events WHERE source = ? AND external_id = ?",
        (source, external_id),
    ).fetchone()
    return row is not None


def seen_event_add(conn: sqlite3.Connection, source: str, external_id: str) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO seen_events(source, external_id, first_seen_at)
        VALUES(?, ?, ?)
        """,
        (source, external_id, utc_now().isoformat()),
    )
    conn.commit()

