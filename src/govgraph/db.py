from __future__ import annotations

import json
import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


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


class ThreadSafeDatabase:
    """Thread-safe wrapper for SQLite database operations."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None

    def _get_connection(self) -> sqlite3.Connection:
        """Get or create the database connection."""
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """Execute a SQL statement with thread safety."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.execute(sql, params)
                return cursor
            except sqlite3.Error as e:
                logger.error(f"Database error executing SQL: {e}", extra={"sql": sql[:100]})
                raise

    def executemany(self, sql: str, params_list: list[tuple]) -> sqlite3.Cursor:
        """Execute a SQL statement with multiple parameter sets."""
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.executemany(sql, params_list)
                return cursor
            except sqlite3.Error as e:
                logger.error(f"Database error executing SQL: {e}", extra={"sql": sql[:100]})
                raise

    def commit(self) -> None:
        """Commit the current transaction."""
        with self._lock:
            if self._conn:
                self._conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None


def connect(db_path: str) -> ThreadSafeDatabase:
    """Create a thread-safe database connection."""
    return ThreadSafeDatabase(db_path)


def init_db(db: ThreadSafeDatabase) -> None:
    """Initialize database tables."""
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS kv_store (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )
        """
    )
    db.execute(
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
    db.execute(
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
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS seen_events (
          source TEXT NOT NULL,
          external_id TEXT NOT NULL,
          first_seen_at TEXT NOT NULL,
          PRIMARY KEY (source, external_id)
        )
        """
    )
    # Add table for webhook delivery tracking (for retry logic)
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS webhook_deliveries (
          id TEXT PRIMARY KEY,
          subscription_id TEXT NOT NULL,
          event_id TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          status TEXT NOT NULL,
          attempts INTEGER NOT NULL DEFAULT 0,
          last_attempt_at TEXT,
          next_retry_at TEXT,
          created_at TEXT NOT NULL,
          FOREIGN KEY (subscription_id) REFERENCES webhook_subscriptions(id)
        )
        """
    )
    db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_status
        ON webhook_deliveries(status, next_retry_at)
        """
    )
    db.commit()
    logger.info("Database initialized successfully")


def kv_get(db: ThreadSafeDatabase, key: str) -> str | None:
    """Get a value from the key-value store."""
    row = db.execute("SELECT value FROM kv_store WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def kv_set(db: ThreadSafeDatabase, key: str, value: str) -> None:
    """Set a value in the key-value store."""
    now = utc_now().isoformat()
    db.execute(
        """
        INSERT INTO kv_store(key, value, updated_at)
        VALUES(?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
        """,
        (key, value, now),
    )
    db.commit()


def cache_get(db: ThreadSafeDatabase, key: str) -> CachedResponse | None:
    """Get a cached response."""
    row = db.execute("SELECT * FROM http_cache WHERE key = ?", (key,)).fetchone()
    if not row:
        return None
    try:
        return CachedResponse(
            status_code=int(row["status_code"]),
            headers=json.loads(row["headers_json"]),
            body=json.loads(row["body_json"]),
            fetched_at=datetime.fromisoformat(row["fetched_at"]),
            ttl_seconds=int(row["ttl_seconds"]),
        )
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning(f"Failed to parse cached response for key {key}: {e}")
        return None


def cache_set(db: ThreadSafeDatabase, key: str, response: CachedResponse) -> None:
    """Store a response in the cache."""
    db.execute(
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
    db.commit()


def seen_event_exists(db: ThreadSafeDatabase, source: str, external_id: str) -> bool:
    """Check if an event has been seen before."""
    row = db.execute(
        "SELECT 1 FROM seen_events WHERE source = ? AND external_id = ?",
        (source, external_id),
    ).fetchone()
    return row is not None


def seen_event_add(db: ThreadSafeDatabase, source: str, external_id: str) -> bool:
    """
    Add a seen event atomically using INSERT OR IGNORE.
    Returns True if the event was newly added, False if it already existed.
    """
    cursor = db.execute(
        """
        INSERT OR IGNORE INTO seen_events(source, external_id, first_seen_at)
        VALUES(?, ?, ?)
        """,
        (source, external_id, utc_now().isoformat()),
    )
    db.commit()
    return cursor.rowcount > 0


# Webhook delivery tracking functions
def create_webhook_delivery(
    db: ThreadSafeDatabase,
    delivery_id: str,
    subscription_id: str,
    event_id: str,
    payload: dict[str, Any],
) -> None:
    """Create a new webhook delivery record."""
    now = utc_now().isoformat()
    db.execute(
        """
        INSERT INTO webhook_deliveries(id, subscription_id, event_id, payload_json, status, attempts, created_at)
        VALUES(?, ?, ?, ?, 'pending', 0, ?)
        """,
        (delivery_id, subscription_id, event_id, json.dumps(payload, sort_keys=True), now),
    )
    db.commit()


def update_webhook_delivery(
    db: ThreadSafeDatabase,
    delivery_id: str,
    status: str,
    next_retry_at: datetime | None = None,
) -> None:
    """Update a webhook delivery status."""
    now = utc_now().isoformat()
    db.execute(
        """
        UPDATE webhook_deliveries
        SET status = ?,
            attempts = attempts + 1,
            last_attempt_at = ?,
            next_retry_at = ?
        WHERE id = ?
        """,
        (status, now, next_retry_at.isoformat() if next_retry_at else None, delivery_id),
    )
    db.commit()


def get_pending_webhook_deliveries(db: ThreadSafeDatabase, limit: int = 100) -> list[dict[str, Any]]:
    """Get pending webhook deliveries that are ready for retry."""
    now = utc_now().isoformat()
    rows = db.execute(
        """
        SELECT wd.*, ws.url, ws.secret, ws.event_type
        FROM webhook_deliveries wd
        JOIN webhook_subscriptions ws ON wd.subscription_id = ws.id
        WHERE wd.status IN ('pending', 'failed')
          AND (wd.next_retry_at IS NULL OR wd.next_retry_at <= ?)
          AND wd.attempts < 5
          AND ws.active = 1
        ORDER BY wd.created_at
        LIMIT ?
        """,
        (now, limit),
    ).fetchall()
    return [dict(row) for row in rows]
