from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, AsyncIterator, Dict, Iterable, List, Optional

import aiomysql

from .config import Settings

logger = logging.getLogger(__name__)


def mask_secret(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    if len(text) <= 10:
        return "*" * len(text)
    return f"{text[:4]}...{text[-4:]}"


CREATE_COLLECTOR_CLIENTS_SQL = """
CREATE TABLE IF NOT EXISTS collector_clients (
    client_id VARCHAR(128) NOT NULL,
    bili_sessdata LONGTEXT NOT NULL,
    enabled TINYINT(1) NOT NULL DEFAULT 1,
    max_active_rooms INT NOT NULL DEFAULT 50,
    remark VARCHAR(255) NOT NULL DEFAULT '',
    last_seen_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (client_id),
    KEY idx_collector_clients_last_seen (last_seen_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

CREATE_ROOM_COLLECT_LEASES_SQL = """
CREATE TABLE IF NOT EXISTS room_collect_leases (
    room_id BIGINT NOT NULL,
    configured_room_id BIGINT NULL,
    client_id VARCHAR(128) NOT NULL,
    run_id BIGINT NULL,
    lease_expires_at DATETIME NOT NULL,
    last_heartbeat_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (room_id),
    KEY idx_room_collect_leases_client (client_id),
    KEY idx_room_collect_leases_run (run_id),
    KEY idx_room_collect_leases_expires (lease_expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

CREATE_ROOMS_SQL = """
CREATE TABLE IF NOT EXISTS rooms (
    id BIGINT NOT NULL AUTO_INCREMENT,
    room_id BIGINT NOT NULL,
    real_room_id BIGINT NULL,
    room_title VARCHAR(255) NOT NULL DEFAULT '',
    anchor_uid BIGINT NULL,
    enabled TINYINT(1) NOT NULL DEFAULT 1,
    live_status TINYINT NOT NULL DEFAULT 0,
    current_session_id BIGINT NULL,
    remark VARCHAR(255) NOT NULL DEFAULT '',
    last_live_at DATETIME NULL,
    last_checked_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uniq_rooms_room_id (room_id),
    KEY idx_rooms_enabled_live_status (enabled, live_status),
    KEY idx_rooms_real_room_id (real_room_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

CREATE_ROOM_EVENTS_SQL = """
CREATE TABLE IF NOT EXISTS room_events (
    id BIGINT NOT NULL AUTO_INCREMENT,
    room_id BIGINT NOT NULL,
    event_type VARCHAR(50) NOT NULL,
    event_key VARCHAR(191) NULL,
    live_session_id BIGINT NULL,
    uid BIGINT NULL,
    username VARCHAR(255) NOT NULL DEFAULT '',
    content TEXT NULL,
    raw_json LONGTEXT NOT NULL,
    event_time DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uniq_room_event_key (room_id, event_type, event_key),
    KEY idx_room_events_session_created (live_session_id, created_at),
    KEY idx_room_events_room_created (room_id, created_at),
    KEY idx_room_events_type_created (event_type, created_at),
    KEY idx_room_events_room_id (room_id, id),
    KEY idx_room_events_type_id (event_type, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

CREATE_EVENT_PROJECTION_STATE_SQL = """
CREATE TABLE IF NOT EXISTS event_projection_state (
    name VARCHAR(64) NOT NULL,
    last_event_id BIGINT NOT NULL DEFAULT 0,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

CREATE_EVENT_TIME_DETAILS_SQL = """
CREATE TABLE IF NOT EXISTS event_time_details (
    event_id BIGINT NOT NULL,
    event_type VARCHAR(50) NOT NULL,
    room_id BIGINT NOT NULL,
    configured_room_id BIGINT NULL,
    live_session_id BIGINT NULL,
    anchor_uid BIGINT NULL,
    uid BIGINT NULL,
    event_at DATETIME NOT NULL,
    ingested_at DATETIME NOT NULL,
    timezone VARCHAR(64) NOT NULL,
    date_key INT NOT NULL,
    hour_key INT NOT NULL,
    minute_key BIGINT NOT NULL,
    event_date DATE NOT NULL,
    hour_start_at DATETIME NOT NULL,
    minute_start_at DATETIME NOT NULL,
    year SMALLINT NOT NULL,
    quarter TINYINT NOT NULL,
    month TINYINT NOT NULL,
    day TINYINT NOT NULL,
    hour TINYINT NOT NULL,
    minute TINYINT NOT NULL,
    second TINYINT NOT NULL,
    weekday TINYINT NOT NULL,
    iso_week TINYINT NOT NULL,
    is_weekend TINYINT(1) NOT NULL,
    session_started_at DATETIME NULL,
    session_elapsed_seconds BIGINT NULL,
    ingest_delay_seconds BIGINT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (event_id),
    KEY idx_event_time_details_type_date (event_type, date_key),
    KEY idx_event_time_details_room_minute (room_id, minute_key),
    KEY idx_event_time_details_session_minute (live_session_id, minute_key),
    KEY idx_event_time_details_uid_date (uid, date_key),
    KEY idx_event_time_details_anchor_date (anchor_uid, date_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

CREATE_DANMAKU_EVENTS_SQL = """
CREATE TABLE IF NOT EXISTS danmaku_events (
    event_id BIGINT NOT NULL,
    room_id BIGINT NOT NULL,
    configured_room_id BIGINT NULL,
    live_session_id BIGINT NULL,
    anchor_uid BIGINT NULL,
    uid BIGINT NULL,
    username VARCHAR(255) NOT NULL DEFAULT '',
    event_at DATETIME NOT NULL,
    created_at DATETIME NOT NULL,
    source_cmd VARCHAR(80) NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    content_length INT NOT NULL DEFAULT 0,
    user_level INT NULL,
    wealth_level INT NULL,
    medal_name VARCHAR(255) NOT NULL DEFAULT '',
    medal_level INT NULL,
    medal_anchor_uid BIGINT NULL,
    guard_level INT NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (event_id),
    KEY idx_danmaku_events_room_time (room_id, event_at),
    KEY idx_danmaku_events_session_time (live_session_id, event_at),
    KEY idx_danmaku_events_uid_time (uid, event_at),
    KEY idx_danmaku_events_anchor_time (anchor_uid, event_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

CREATE_ENTER_ROOM_EVENTS_SQL = """
CREATE TABLE IF NOT EXISTS enter_room_events (
    event_id BIGINT NOT NULL,
    room_id BIGINT NOT NULL,
    configured_room_id BIGINT NULL,
    live_session_id BIGINT NULL,
    anchor_uid BIGINT NULL,
    uid BIGINT NULL,
    username VARCHAR(255) NOT NULL DEFAULT '',
    event_at DATETIME NOT NULL,
    created_at DATETIME NOT NULL,
    source_cmd VARCHAR(80) NOT NULL DEFAULT '',
    action_text VARCHAR(255) NOT NULL DEFAULT '',
    user_level INT NULL,
    wealth_level INT NULL,
    medal_name VARCHAR(255) NOT NULL DEFAULT '',
    medal_level INT NULL,
    medal_anchor_uid BIGINT NULL,
    guard_level INT NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (event_id),
    KEY idx_enter_room_events_room_time (room_id, event_at),
    KEY idx_enter_room_events_session_time (live_session_id, event_at),
    KEY idx_enter_room_events_uid_time (uid, event_at),
    KEY idx_enter_room_events_anchor_time (anchor_uid, event_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

CREATE_FOLLOW_EVENTS_SQL = """
CREATE TABLE IF NOT EXISTS follow_events (
    event_id BIGINT NOT NULL,
    room_id BIGINT NOT NULL,
    configured_room_id BIGINT NULL,
    live_session_id BIGINT NULL,
    anchor_uid BIGINT NULL,
    uid BIGINT NULL,
    username VARCHAR(255) NOT NULL DEFAULT '',
    event_at DATETIME NOT NULL,
    created_at DATETIME NOT NULL,
    source_cmd VARCHAR(80) NOT NULL DEFAULT '',
    action_text VARCHAR(255) NOT NULL DEFAULT '',
    user_level INT NULL,
    wealth_level INT NULL,
    medal_name VARCHAR(255) NOT NULL DEFAULT '',
    medal_level INT NULL,
    medal_anchor_uid BIGINT NULL,
    guard_level INT NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (event_id),
    KEY idx_follow_events_room_time (room_id, event_at),
    KEY idx_follow_events_session_time (live_session_id, event_at),
    KEY idx_follow_events_uid_time (uid, event_at),
    KEY idx_follow_events_anchor_time (anchor_uid, event_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

CREATE_GIFT_EVENTS_SQL = """
CREATE TABLE IF NOT EXISTS gift_events (
    event_id BIGINT NOT NULL,
    room_id BIGINT NOT NULL,
    configured_room_id BIGINT NULL,
    live_session_id BIGINT NULL,
    anchor_uid BIGINT NULL,
    uid BIGINT NULL,
    username VARCHAR(255) NOT NULL DEFAULT '',
    event_at DATETIME NOT NULL,
    created_at DATETIME NOT NULL,
    source_cmd VARCHAR(80) NOT NULL DEFAULT '',
    gift_id BIGINT NULL,
    gift_name VARCHAR(255) NOT NULL DEFAULT '',
    gift_num INT NOT NULL DEFAULT 0,
    gift_price BIGINT NULL,
    gift_total_price BIGINT NULL,
    coin_type VARCHAR(32) NOT NULL DEFAULT '',
    batch_combo_id VARCHAR(191) NOT NULL DEFAULT '',
    combo_id VARCHAR(191) NOT NULL DEFAULT '',
    user_level INT NULL,
    wealth_level INT NULL,
    medal_name VARCHAR(255) NOT NULL DEFAULT '',
    medal_level INT NULL,
    medal_anchor_uid BIGINT NULL,
    guard_level INT NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (event_id),
    KEY idx_gift_events_room_time (room_id, event_at),
    KEY idx_gift_events_session_time (live_session_id, event_at),
    KEY idx_gift_events_uid_time (uid, event_at),
    KEY idx_gift_events_anchor_time (anchor_uid, event_at),
    KEY idx_gift_events_gift_time (gift_id, event_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

CREATE_GUARD_EVENTS_SQL = """
CREATE TABLE IF NOT EXISTS guard_events (
    event_id BIGINT NOT NULL,
    room_id BIGINT NOT NULL,
    configured_room_id BIGINT NULL,
    live_session_id BIGINT NULL,
    anchor_uid BIGINT NULL,
    uid BIGINT NULL,
    username VARCHAR(255) NOT NULL DEFAULT '',
    event_at DATETIME NOT NULL,
    created_at DATETIME NOT NULL,
    source_cmd VARCHAR(80) NOT NULL DEFAULT '',
    guard_level INT NULL,
    guard_name VARCHAR(64) NOT NULL DEFAULT '',
    guard_num INT NOT NULL DEFAULT 0,
    gift_id BIGINT NULL,
    gift_name VARCHAR(255) NOT NULL DEFAULT '',
    price BIGINT NULL,
    total_price BIGINT NULL,
    user_level INT NULL,
    wealth_level INT NULL,
    medal_name VARCHAR(255) NOT NULL DEFAULT '',
    medal_level INT NULL,
    medal_anchor_uid BIGINT NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (event_id),
    KEY idx_guard_events_room_time (room_id, event_at),
    KEY idx_guard_events_session_time (live_session_id, event_at),
    KEY idx_guard_events_uid_time (uid, event_at),
    KEY idx_guard_events_anchor_time (anchor_uid, event_at),
    KEY idx_guard_events_level_time (guard_level, event_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

CREATE_SUPER_CHAT_EVENTS_SQL = """
CREATE TABLE IF NOT EXISTS super_chat_events (
    event_id BIGINT NOT NULL,
    room_id BIGINT NOT NULL,
    configured_room_id BIGINT NULL,
    live_session_id BIGINT NULL,
    anchor_uid BIGINT NULL,
    uid BIGINT NULL,
    username VARCHAR(255) NOT NULL DEFAULT '',
    event_at DATETIME NOT NULL,
    created_at DATETIME NOT NULL,
    source_cmd VARCHAR(80) NOT NULL DEFAULT '',
    super_chat_id BIGINT NULL,
    message TEXT NOT NULL,
    message_length INT NOT NULL DEFAULT 0,
    price DECIMAL(12,2) NULL,
    start_time DATETIME NULL,
    end_time DATETIME NULL,
    duration_seconds INT NULL,
    user_level INT NULL,
    wealth_level INT NULL,
    medal_name VARCHAR(255) NOT NULL DEFAULT '',
    medal_level INT NULL,
    medal_anchor_uid BIGINT NULL,
    guard_level INT NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (event_id),
    KEY idx_super_chat_events_room_time (room_id, event_at),
    KEY idx_super_chat_events_session_time (live_session_id, event_at),
    KEY idx_super_chat_events_uid_time (uid, event_at),
    KEY idx_super_chat_events_anchor_time (anchor_uid, event_at),
    KEY idx_super_chat_events_price_time (price, event_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

CREATE_LIVE_SESSIONS_SQL = """
CREATE TABLE IF NOT EXISTS live_sessions (
    id BIGINT NOT NULL AUTO_INCREMENT,
    room_id BIGINT NOT NULL,
    configured_room_id BIGINT NULL,
    anchor_uid BIGINT NULL,
    room_title VARCHAR(255) NOT NULL DEFAULT '',
    started_at DATETIME NOT NULL,
    detected_started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at DATETIME NULL,
    detected_ended_at DATETIME NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'live',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_live_sessions_room_started (room_id, started_at),
    KEY idx_live_sessions_configured_started (configured_room_id, started_at),
    KEY idx_live_sessions_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

CREATE_MONITOR_RUNS_SQL = """
CREATE TABLE IF NOT EXISTS monitor_runs (
    id BIGINT NOT NULL AUTO_INCREMENT,
    room_id BIGINT NOT NULL,
    configured_room_id BIGINT NULL,
    client_id VARCHAR(128) NULL,
    running TINYINT(1) NOT NULL DEFAULT 1,
    started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_heartbeat_at DATETIME NULL,
    stopped_at DATETIME NULL,
    error_message TEXT NULL,
    reconnect_count INT NOT NULL DEFAULT 0,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_monitor_runs_room_running (room_id, running),
    KEY idx_monitor_runs_client_running (client_id, running),
    KEY idx_monitor_runs_started_at (started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


@dataclass(frozen=True)
class EventRecord:
    """入库前的标准化事件结构。"""

    room_id: int
    event_type: str
    event_key: Optional[str]
    uid: Optional[int]
    username: str
    content: str
    raw: Dict[str, Any]
    event_time: Optional[datetime]


ANALYTIC_EVENT_TABLES = frozenset(
    {
        "danmaku_events",
        "enter_room_events",
        "follow_events",
        "gift_events",
        "guard_events",
        "super_chat_events",
    }
)


class Database:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._pool: Optional[aiomysql.Pool] = None

    async def connect(self) -> None:
        self._pool = await aiomysql.create_pool(
            host=self._settings.mysql_host,
            port=self._settings.mysql_port,
            user=self._settings.mysql_user,
            password=self._settings.mysql_password,
            db=self._settings.mysql_database,
            charset="utf8mb4",
            autocommit=True,
            minsize=1,
            maxsize=10,
        )

    async def close(self) -> None:
        if self._pool is None:
            return
        self._pool.close()
        await self._pool.wait_closed()
        self._pool = None

    async def migrate(self) -> None:
        await self.execute(CREATE_COLLECTOR_CLIENTS_SQL)
        await self.execute(CREATE_ROOMS_SQL)
        await self.execute(CREATE_LIVE_SESSIONS_SQL)
        await self.execute(CREATE_ROOM_EVENTS_SQL)
        await self.execute(CREATE_EVENT_PROJECTION_STATE_SQL)
        await self.execute(CREATE_EVENT_TIME_DETAILS_SQL)
        await self.execute(CREATE_DANMAKU_EVENTS_SQL)
        await self.execute(CREATE_ENTER_ROOM_EVENTS_SQL)
        await self.execute(CREATE_FOLLOW_EVENTS_SQL)
        await self.execute(CREATE_GIFT_EVENTS_SQL)
        await self.execute(CREATE_GUARD_EVENTS_SQL)
        await self.execute(CREATE_SUPER_CHAT_EVENTS_SQL)
        await self.execute(CREATE_MONITOR_RUNS_SQL)
        await self.execute(CREATE_ROOM_COLLECT_LEASES_SQL)
        await self.ensure_column("collector_clients", "enabled", "TINYINT(1) NOT NULL DEFAULT 1 AFTER bili_sessdata")
        await self.ensure_column("collector_clients", "max_active_rooms", "INT NOT NULL DEFAULT 50 AFTER enabled")
        await self.ensure_column("rooms", "current_session_id", "BIGINT NULL AFTER live_status")
        await self.ensure_column("room_events", "live_session_id", "BIGINT NULL AFTER event_key")
        await self.ensure_index("room_events", "idx_room_events_session_created", "KEY idx_room_events_session_created (live_session_id, created_at)")
        await self.ensure_index("room_events", "idx_room_events_room_id", "KEY idx_room_events_room_id (room_id, id)")
        await self.ensure_index("room_events", "idx_room_events_type_id", "KEY idx_room_events_type_id (event_type, id)")
        await self.ensure_column("monitor_runs", "configured_room_id", "BIGINT NULL AFTER room_id")
        await self.ensure_column("monitor_runs", "client_id", "VARCHAR(128) NULL AFTER configured_room_id")
        await self.ensure_column("monitor_runs", "last_heartbeat_at", "DATETIME NULL AFTER started_at")
        await self.backfill_room_collect_leases()
        # 老数据可能曾经按 UTC 少存了 8 小时。这里用时间差条件保护，
        # 保证迁移可以重复执行，不会把已经正确的行再次平移。
        await self.execute(
            """
            UPDATE room_events
            SET event_time = DATE_ADD(event_time, INTERVAL 8 HOUR)
            WHERE event_type IN ('danmaku', 'enter_room', 'gift', 'guard', 'super_chat')
              AND event_time IS NOT NULL
              AND created_at IS NOT NULL
              AND TIMESTAMPDIFF(MINUTE, event_time, created_at) BETWEEN 420 AND 540
            """
        )
        await self.execute(
            """
            UPDATE monitor_runs
            SET started_at = DATE_ADD(started_at, INTERVAL 8 HOUR)
            WHERE started_at IS NOT NULL
              AND created_at IS NOT NULL
              AND TIMESTAMPDIFF(MINUTE, started_at, created_at) BETWEEN 420 AND 540
            """
        )
        await self.execute(
            """
            UPDATE monitor_runs
            SET stopped_at = DATE_ADD(stopped_at, INTERVAL 8 HOUR)
            WHERE stopped_at IS NOT NULL
              AND updated_at IS NOT NULL
              AND TIMESTAMPDIFF(MINUTE, stopped_at, updated_at) BETWEEN 420 AND 540
            """
        )
        await self.execute(
            """
            UPDATE rooms
            SET last_checked_at = DATE_ADD(last_checked_at, INTERVAL 8 HOUR)
            WHERE last_checked_at IS NOT NULL
              AND updated_at IS NOT NULL
              AND TIMESTAMPDIFF(MINUTE, last_checked_at, updated_at) BETWEEN 420 AND 540
            """
        )
        await self.execute(
            """
            UPDATE rooms
            SET last_live_at = DATE_ADD(last_live_at, INTERVAL 8 HOUR)
            WHERE last_live_at IS NOT NULL
              AND updated_at IS NOT NULL
              AND TIMESTAMPDIFF(MINUTE, last_live_at, updated_at) BETWEEN 420 AND 540
            """
        )

    async def touch_collector_client(self, client_id: str) -> None:
        await self.execute(
            """
            INSERT INTO collector_clients (client_id, bili_sessdata, last_seen_at)
            VALUES (%s, '', CURRENT_TIMESTAMP)
            ON DUPLICATE KEY UPDATE
                last_seen_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            """,
            (client_id,),
        )

    async def upsert_collector_client(
        self,
        *,
        client_id: str,
        bili_sessdata: Optional[str],
        enabled: Optional[bool],
        max_active_rooms: Optional[int],
        remark: Optional[str],
    ) -> Dict[str, Any]:
        max_rooms = max(1, int(max_active_rooms or 50))
        await self.execute(
            """
            INSERT INTO collector_clients (client_id, bili_sessdata, enabled, max_active_rooms, remark)
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                bili_sessdata = CASE
                    WHEN %s IS NULL THEN bili_sessdata
                    ELSE VALUES(bili_sessdata)
                END,
                enabled = CASE
                    WHEN %s IS NULL THEN enabled
                    ELSE VALUES(enabled)
                END,
                max_active_rooms = CASE
                    WHEN %s IS NULL THEN max_active_rooms
                    ELSE VALUES(max_active_rooms)
                END,
                remark = VALUES(remark),
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                client_id,
                "" if bili_sessdata is None else bili_sessdata.strip(),
                1 if enabled is None else int(enabled),
                max_rooms,
                remark or "",
                bili_sessdata,
                enabled,
                max_active_rooms,
            ),
        )
        client = await self.get_collector_client(client_id)
        if client is None:
            raise RuntimeError("failed to load collector client")
        return client

    async def get_collector_client(self, client_id: str) -> Optional[Dict[str, Any]]:
        row = await self.fetch_one(
            """
            SELECT client_id, bili_sessdata, enabled, max_active_rooms, remark, last_seen_at, created_at, updated_at
            FROM collector_clients
            WHERE client_id = %s
            """,
            (client_id,),
        )
        return self._collector_client_public(row) if row else None

    async def get_collector_client_sessdata(self, client_id: str) -> str:
        row = await self.fetch_one(
            "SELECT bili_sessdata FROM collector_clients WHERE client_id = %s",
            (client_id,),
        )
        return str(row["bili_sessdata"] or "") if row else ""

    async def get_collector_client_runtime_config(self, client_id: str) -> Dict[str, Any]:
        row = await self.fetch_one(
            """
            SELECT bili_sessdata, enabled, max_active_rooms
            FROM collector_clients
            WHERE client_id = %s
            """,
            (client_id,),
        )
        if row is None:
            return {"bili_sessdata": "", "enabled": True, "max_active_rooms": 50}
        return {
            "bili_sessdata": str(row.get("bili_sessdata") or ""),
            "enabled": bool(row.get("enabled")),
            "max_active_rooms": max(1, int(row.get("max_active_rooms") or 50)),
        }

    async def list_collector_clients(self) -> List[Dict[str, Any]]:
        rows = await self.fetch_all(
            """
            SELECT client_id, bili_sessdata, enabled, max_active_rooms, remark, last_seen_at, created_at, updated_at
            FROM collector_clients
            ORDER BY COALESCE(last_seen_at, updated_at) DESC, client_id ASC
            """
        )
        return [self._collector_client_public(row) for row in rows]

    def _collector_client_public(self, row: Dict[str, Any]) -> Dict[str, Any]:
        value = str(row.get("bili_sessdata") or "")
        return {
            "client_id": row["client_id"],
            "remark": row.get("remark") or "",
            "enabled": bool(row.get("enabled")),
            "max_active_rooms": max(1, int(row.get("max_active_rooms") or 50)),
            "sessdata_configured": bool(value),
            "sessdata_preview": mask_secret(value),
            "last_seen_at": row.get("last_seen_at"),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }

    async def reclassify_unknown_events(self, batch_size: int = 1000) -> int:
        from .events import parse_event_or_error

        updated = 0
        last_id = 0
        while True:
            rows = await self.fetch_all(
                """
                SELECT id, room_id, raw_json
                FROM room_events
                WHERE event_type = 'unknown'
                  AND id > %s
                ORDER BY id ASC
                LIMIT %s
                """,
                (last_id, batch_size),
            )
            if not rows:
                break
            last_id = int(rows[-1]["id"])
            for row in rows:
                try:
                    raw = json.loads(row["raw_json"])
                except (TypeError, json.JSONDecodeError):
                    continue
                if not isinstance(raw, dict):
                    continue
                event = parse_event_or_error(int(row["room_id"]), raw)
                if event is None or event.event_type in ("unknown", "parse_error"):
                    continue
                rowcount = await self.execute(
                    """
                    UPDATE room_events
                    SET event_type = %s,
                        uid = %s,
                        username = %s,
                        content = %s,
                        event_time = COALESCE(%s, event_time)
                    WHERE id = %s
                      AND event_type = 'unknown'
                    """,
                    (
                        event.event_type,
                        event.uid,
                        event.username,
                        event.content,
                        event.event_time,
                        row["id"],
                    ),
                )
                updated += rowcount
        return updated

    async def ensure_column(self, table_name: str, column_name: str, ddl: str) -> None:
        row = await self.fetch_one(
            """
            SELECT COUNT(*) AS count
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = %s
              AND TABLE_NAME = %s
              AND COLUMN_NAME = %s
            """,
            (self._settings.mysql_database, table_name, column_name),
        )
        if row and int(row["count"]) > 0:
            return
        await self.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}")

    async def ensure_index(self, table_name: str, index_name: str, ddl: str) -> None:
        row = await self.fetch_one(
            """
            SELECT COUNT(*) AS count
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = %s
              AND TABLE_NAME = %s
              AND INDEX_NAME = %s
            """,
            (self._settings.mysql_database, table_name, index_name),
        )
        if row and int(row["count"]) > 0:
            return
        await self.execute(f"ALTER TABLE {table_name} ADD {ddl}")

    @asynccontextmanager
    async def named_lock(self, lock_name: str, timeout_seconds: int = 0) -> AsyncIterator[bool]:
        if self._pool is None:
            raise RuntimeError("database is not connected")
        async with self._pool.acquire() as conn:
            acquired = False
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute("SELECT GET_LOCK(%s, %s) AS acquired", (lock_name, timeout_seconds))
                row = await cursor.fetchone()
                acquired = bool(row and int(row["acquired"]) == 1)
            try:
                yield acquired
            finally:
                if acquired:
                    async with conn.cursor() as cursor:
                        await cursor.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))

    async def get_projection_last_event_id(self, name: str) -> int:
        await self.execute(
            """
            INSERT INTO event_projection_state (name, last_event_id)
            VALUES (%s, 0)
            ON DUPLICATE KEY UPDATE name = VALUES(name)
            """,
            (name,),
        )
        row = await self.fetch_one(
            "SELECT last_event_id FROM event_projection_state WHERE name = %s",
            (name,),
        )
        return int(row["last_event_id"]) if row else 0

    async def update_projection_last_event_id(self, name: str, last_event_id: int) -> None:
        await self.execute(
            """
            INSERT INTO event_projection_state (name, last_event_id)
            VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE
                last_event_id = GREATEST(last_event_id, VALUES(last_event_id)),
                updated_at = CURRENT_TIMESTAMP
            """,
            (name, last_event_id),
        )

    async def fetch_room_events_after(self, last_event_id: int, limit: int) -> List[Dict[str, Any]]:
        return await self.fetch_all(
            """
            SELECT id,
                   room_id,
                   event_type,
                   event_key,
                   live_session_id,
                   uid,
                   username,
                   content,
                   raw_json,
                   event_time,
                   created_at
            FROM room_events
            WHERE id > %s
            ORDER BY id ASC
            LIMIT %s
            """,
            (last_event_id, limit),
        )

    async def resolve_event_projection_context(
        self,
        *,
        room_id: int,
        event_at: datetime,
        live_session_id: Optional[int],
    ) -> Dict[str, Any]:
        session = None
        if live_session_id is not None:
            session = await self.fetch_one(
                """
                SELECT id, configured_room_id, anchor_uid, started_at
                FROM live_sessions
                WHERE id = %s
                LIMIT 1
                """,
                (live_session_id,),
            )
        if session is None:
            session = await self.fetch_one(
                """
                SELECT id, configured_room_id, anchor_uid, started_at
                FROM live_sessions
                WHERE room_id = %s
                  AND started_at <= %s
                  AND (ended_at IS NULL OR ended_at >= %s)
                ORDER BY started_at DESC, id DESC
                LIMIT 1
                """,
                (room_id, event_at, event_at),
            )
        if session is not None:
            return {
                "live_session_id": session["id"],
                "configured_room_id": session["configured_room_id"],
                "anchor_uid": session["anchor_uid"],
                "session_started_at": session["started_at"],
            }

        room = await self.fetch_one(
            """
            SELECT room_id AS configured_room_id, anchor_uid
            FROM rooms
            WHERE real_room_id = %s OR room_id = %s
            ORDER BY CASE WHEN real_room_id = %s THEN 0 ELSE 1 END
            LIMIT 1
            """,
            (room_id, room_id, room_id),
        )
        return {
            "live_session_id": None,
            "configured_room_id": room["configured_room_id"] if room else None,
            "anchor_uid": room["anchor_uid"] if room else None,
            "session_started_at": None,
        }

    async def upsert_event_time_detail(self, row: Dict[str, Any]) -> None:
        await self.upsert_event_time_details([row])

    async def upsert_event_time_details(self, rows: List[Dict[str, Any]]) -> None:
        await self._upsert_projection_rows("event_time_details", rows)

    async def upsert_analytic_event(self, table_name: str, row: Dict[str, Any]) -> None:
        await self.upsert_analytic_events(table_name, [row])

    async def upsert_analytic_events(self, table_name: str, rows: List[Dict[str, Any]]) -> None:
        if table_name not in ANALYTIC_EVENT_TABLES:
            raise ValueError(f"unsupported analytic event table: {table_name}")
        await self._upsert_projection_rows(table_name, rows)

    async def _upsert_projection_rows(self, table_name: str, rows: List[Dict[str, Any]]) -> None:
        if not rows:
            return
        if table_name != "event_time_details" and table_name not in ANALYTIC_EVENT_TABLES:
            raise ValueError(f"unsupported projection table: {table_name}")
        row = rows[0]
        columns = list(row.keys())
        for current in rows:
            if list(current.keys()) != columns:
                raise ValueError(f"inconsistent projection columns for {table_name}")
        placeholders = ", ".join(["%s"] * len(columns))
        column_sql = ", ".join(f"`{column}`" for column in columns)
        update_sql = ", ".join(f"`{column}` = VALUES(`{column}`)" for column in columns if column != "event_id")
        await self.execute_many(
            f"""
            INSERT INTO {table_name} ({column_sql})
            VALUES ({placeholders})
            ON DUPLICATE KEY UPDATE {update_sql}
            """,
            [tuple(current[column] for column in columns) for current in rows],
        )

    async def backfill_room_collect_leases(self) -> None:
        await self.execute(
            """
            INSERT INTO room_collect_leases
                (room_id, configured_room_id, client_id, run_id, lease_expires_at, last_heartbeat_at)
            SELECT room_id,
                   configured_room_id,
                   COALESCE(client_id, ''),
                   id,
                   DATE_ADD(CURRENT_TIMESTAMP, INTERVAL %s SECOND),
                   COALESCE(last_heartbeat_at, started_at)
            FROM monitor_runs
            WHERE running = 1
            ON DUPLICATE KEY UPDATE
                configured_room_id = VALUES(configured_room_id),
                client_id = VALUES(client_id),
                run_id = VALUES(run_id),
                lease_expires_at = VALUES(lease_expires_at),
                last_heartbeat_at = VALUES(last_heartbeat_at),
                updated_at = CURRENT_TIMESTAMP
            """,
            (self._settings.collector_stale_seconds,),
        )

    async def execute(self, sql: str, params: Optional[Iterable[Any]] = None) -> int:
        if self._pool is None:
            raise RuntimeError("database is not connected")
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(sql, params)
                return cursor.rowcount

    async def execute_many(self, sql: str, params: Iterable[Iterable[Any]]) -> int:
        if self._pool is None:
            raise RuntimeError("database is not connected")
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.executemany(sql, params)
                return cursor.rowcount

    async def fetch_one(self, sql: str, params: Optional[Iterable[Any]] = None) -> Optional[Dict[str, Any]]:
        if self._pool is None:
            raise RuntimeError("database is not connected")
        async with self._pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(sql, params)
                return await cursor.fetchone()

    async def fetch_all(self, sql: str, params: Optional[Iterable[Any]] = None) -> List[Dict[str, Any]]:
        if self._pool is None:
            raise RuntimeError("database is not connected")
        async with self._pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(sql, params)
                return list(await cursor.fetchall())

    async def upsert_room(self, room_id: int, enabled: bool = True, remark: str = "") -> Dict[str, Any]:
        await self.execute(
            """
            INSERT INTO rooms (room_id, enabled, remark)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE
                enabled = VALUES(enabled),
                remark = VALUES(remark),
                updated_at = CURRENT_TIMESTAMP
            """,
            (room_id, int(enabled), remark),
        )
        room = await self.get_room_by_room_id(room_id)
        if room is None:
            raise RuntimeError("failed to load room after upsert")
        return room

    async def get_room(self, room_pk: int) -> Optional[Dict[str, Any]]:
        return await self.fetch_one("SELECT * FROM rooms WHERE id = %s", (room_pk,))

    async def get_room_by_room_id(self, room_id: int) -> Optional[Dict[str, Any]]:
        return await self.fetch_one("SELECT * FROM rooms WHERE room_id = %s", (room_id,))

    async def list_rooms(self, enabled_only: bool = False) -> List[Dict[str, Any]]:
        sql = """
            SELECT r.*,
                   ls.started_at AS current_session_started_at,
                   ls.detected_started_at AS current_session_detected_started_at
            FROM rooms r
            LEFT JOIN live_sessions ls ON ls.id = r.current_session_id
        """
        if enabled_only:
            return await self.fetch_all(f"{sql} WHERE r.enabled = 1 ORDER BY r.id ASC")
        return await self.fetch_all(f"{sql} ORDER BY r.id ASC")

    async def update_room(self, room_pk: int, *, enabled: Optional[bool], remark: Optional[str]) -> Optional[Dict[str, Any]]:
        updates: List[str] = []
        params: List[Any] = []
        if enabled is not None:
            updates.append("enabled = %s")
            params.append(int(enabled))
        if remark is not None:
            updates.append("remark = %s")
            params.append(remark)
        if updates:
            updates.append("updated_at = CURRENT_TIMESTAMP")
            params.append(room_pk)
            await self.execute(f"UPDATE rooms SET {', '.join(updates)} WHERE id = %s", params)
        return await self.get_room(room_pk)

    async def delete_room(self, room_pk: int) -> bool:
        rowcount = await self.execute("DELETE FROM rooms WHERE id = %s", (room_pk,))
        return rowcount > 0

    async def update_room_status(
        self,
        room_id: int,
        *,
        real_room_id: Optional[int],
        room_title: str,
        anchor_uid: Optional[int],
        live_status: int,
        live_started_at: Optional[datetime] = None,
    ) -> None:
        await self.execute(
            """
            UPDATE rooms
            SET real_room_id = %s,
                room_title = %s,
                anchor_uid = %s,
                live_status = %s,
                last_live_at = CASE
                    WHEN %s = 1 THEN COALESCE(%s, last_live_at, CURRENT_TIMESTAMP)
                    ELSE last_live_at
                END,
                last_checked_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE room_id = %s
            """,
            (real_room_id, room_title, anchor_uid, live_status, live_status, live_started_at, room_id),
        )

    async def ensure_live_session(
        self,
        *,
        configured_room_id: int,
        real_room_id: int,
        room_title: str,
        anchor_uid: Optional[int],
        started_at: datetime,
    ) -> Dict[str, Any]:
        # 直播场次以“房间 + 开播时间(B 站 live_time)”为身份：采集中断重连、或被
        # 短暂判定下播后恢复，只要还是同一场直播(开播时间不变)就复用同一条场次，
        # 必要时重新置为直播中，避免一场直播被拆成多条记录。优先匹配相同开播时间，
        # 其次回退到当前仍打开的场次。
        session = await self.fetch_one(
            """
            SELECT *
            FROM live_sessions
            WHERE room_id = %s
              AND (started_at = %s OR (status = 'live' AND ended_at IS NULL))
            ORDER BY (started_at = %s) DESC, id DESC
            LIMIT 1
            """,
            (real_room_id, started_at, started_at),
        )
        if session is None:
            if self._pool is None:
                raise RuntimeError("database is not connected")
            async with self._pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute(
                        """
                        INSERT INTO live_sessions
                            (room_id, configured_room_id, anchor_uid, room_title, started_at)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (real_room_id, configured_room_id, anchor_uid, room_title, started_at),
                    )
                    session_id = int(cursor.lastrowid)
            session = await self.get_live_session(session_id)
        else:
            session_id = int(session["id"])
            await self.execute(
                """
                UPDATE live_sessions
                SET configured_room_id = %s,
                    anchor_uid = %s,
                    room_title = %s,
                    started_at = %s,
                    status = 'live',
                    ended_at = NULL,
                    detected_ended_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (configured_room_id, anchor_uid, room_title, started_at, session_id),
            )
            session = await self.get_live_session(session_id)

        if session is None:
            raise RuntimeError("failed to load live session")
        await self.execute(
            """
            UPDATE rooms
            SET current_session_id = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE room_id = %s
            """,
            (int(session["id"]), configured_room_id),
        )
        return session

    async def finish_live_session(
        self,
        *,
        configured_room_id: int,
        real_room_id: int,
        ended_at: Optional[datetime] = None,
    ) -> None:
        finished_at = ended_at or datetime.now()
        await self.execute(
            """
            UPDATE live_sessions
            SET status = 'ended',
                ended_at = COALESCE(ended_at, %s),
                detected_ended_at = COALESCE(detected_ended_at, CURRENT_TIMESTAMP),
                updated_at = CURRENT_TIMESTAMP
            WHERE room_id = %s
              AND status = 'live'
              AND ended_at IS NULL
            """,
            (finished_at, real_room_id),
        )
        await self.execute(
            """
            UPDATE rooms
            SET current_session_id = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE room_id = %s
              AND current_session_id IS NOT NULL
            """,
            (configured_room_id,),
        )

    async def get_live_session(self, session_id: int) -> Optional[Dict[str, Any]]:
        return await self.fetch_one("SELECT * FROM live_sessions WHERE id = %s", (session_id,))

    async def list_live_sessions(
        self,
        *,
        room_id: Optional[int],
        status: Optional[str],
        limit: int,
        offset: int,
    ) -> List[Dict[str, Any]]:
        filters: List[str] = []
        params: List[Any] = []
        if room_id is not None:
            filters.append("(room_id = %s OR configured_room_id = %s)")
            params.extend([room_id, room_id])
        if status:
            filters.append("status = %s")
            params.append(status)
        where_sql = f"WHERE {' AND '.join(filters)}" if filters else ""
        params.extend([limit, offset])
        return await self.fetch_all(
            f"""
            SELECT id,
                   room_id,
                   configured_room_id,
                   anchor_uid,
                   room_title,
                   started_at,
                   detected_started_at,
                   ended_at,
                   detected_ended_at,
                   status,
                   created_at,
                   updated_at
            FROM live_sessions
            {where_sql}
            ORDER BY started_at DESC, id DESC
            LIMIT %s OFFSET %s
            """,
            params,
        )

    async def touch_room_check(self, room_id: int) -> None:
        await self.execute(
            "UPDATE rooms SET last_checked_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE room_id = %s",
            (room_id,),
        )

    async def insert_event(self, event: EventRecord) -> None:
        # 已知业务事件使用唯一 key，避免重连或重复上报导致重复入库。
        # 未知命令和解析失败会在解析层跳过，不进入这里。
        await self.execute(
            """
            INSERT INTO room_events
                (room_id, event_type, event_key, live_session_id, uid, username, content, raw_json, event_time)
            VALUES (
                %s,
                %s,
                %s,
                (
                    SELECT current_session_id
                    FROM rooms
                    WHERE (real_room_id = %s OR room_id = %s)
                      AND current_session_id IS NOT NULL
                    ORDER BY CASE WHEN real_room_id = %s THEN 0 ELSE 1 END
                    LIMIT 1
                ),
                %s,
                %s,
                %s,
                %s,
                %s
            )
            ON DUPLICATE KEY UPDATE id = id
            """,
            (
                event.room_id,
                event.event_type,
                event.event_key,
                event.room_id,
                event.room_id,
                event.room_id,
                event.uid,
                event.username,
                event.content,
                json.dumps(event.raw, ensure_ascii=False, default=str),
                event.event_time,
            ),
        )

    async def list_events(
        self,
        *,
        room_id: Optional[int],
        event_type: Optional[str],
        start: Optional[datetime],
        end: Optional[datetime],
        before_id: Optional[int] = None,
        live_session_id: Optional[int] = None,
        limit: int,
        offset: int,
    ) -> List[Dict[str, Any]]:
        filters: List[str] = ["event_type NOT IN ('unknown', 'parse_error')"]
        params: List[Any] = []
        if room_id is not None:
            filters.append("room_id = %s")
            params.append(room_id)
        if event_type:
            filters.append("event_type = %s")
            params.append(event_type)
        if start is not None:
            filters.append("created_at >= %s")
            params.append(start)
        if end is not None:
            filters.append("created_at <= %s")
            params.append(end)
        if before_id is not None:
            filters.append("id < %s")
            params.append(before_id)
        if live_session_id is not None:
            filters.append("live_session_id = %s")
            params.append(live_session_id)
        where_sql = f"WHERE {' AND '.join(filters)}" if filters else ""
        params.extend([limit, offset])
        return await self.fetch_all(
            f"""
            SELECT id,
                   room_id,
                   event_type,
                   event_key,
                   live_session_id,
                   uid,
                   username,
                   content,
                   raw_json,
                   event_time,
                   created_at
            FROM room_events
            {where_sql}
            ORDER BY id DESC
            LIMIT %s OFFSET %s
            """,
            params,
        )

    async def analytics_overview(
        self,
        *,
        room_id: Optional[int],
        live_session_id: Optional[int],
        minutes: int,
        bucket_minutes: int,
    ) -> Dict[str, Any]:
        filters: List[str] = []
        params: List[Any] = []
        session_row = None
        if live_session_id is not None:
            session_row = await self.get_live_session(live_session_id)
            filters.append("live_session_id = %s")
            params.append(live_session_id)
        if room_id is not None:
            filters.append("room_id = %s")
            params.append(room_id)
        where_sql = f"WHERE {' AND '.join(filters)}" if filters else ""
        latest = await self.fetch_one(
            f"""
            SELECT MAX(event_at) AS event_at
            FROM event_time_details
            {where_sql}
            """,
            params,
        )
        end_at = latest.get("event_at") if latest else None
        if end_at is None:
            return {
                "range": {
                    "room_id": room_id,
                    "live_session_id": live_session_id,
                    "session": session_row,
                    "start": None,
                    "end": None,
                    "minutes": minutes,
                    "bucket_minutes": bucket_minutes,
                },
                "summary": {"total_events": 0, "unique_users": 0, "by_type": {}, "gift_total_coin": 0},
                "series": [],
                "gift_rank": [],
            }

        start_at = end_at - timedelta(minutes=minutes)
        range_filters = ["event_at >= %s", "event_at <= %s"]
        range_params: List[Any] = [start_at, end_at]
        if live_session_id is not None:
            range_filters.append("live_session_id = %s")
            range_params.append(live_session_id)
        if room_id is not None:
            range_filters.append("room_id = %s")
            range_params.append(room_id)
        range_where_sql = f"WHERE {' AND '.join(range_filters)}"
        bucket_seconds = max(1, bucket_minutes) * 60

        event_rows = await self.fetch_all(
            f"""
            SELECT FROM_UNIXTIME(FLOOR(UNIX_TIMESTAMP(event_at) / %s) * %s) AS bucket_at,
                   event_type,
                   COUNT(*) AS count,
                   COUNT(DISTINCT NULLIF(uid, 0)) AS unique_users
            FROM event_time_details
            {range_where_sql}
            GROUP BY bucket_at, event_type
            ORDER BY bucket_at ASC
            """,
            (bucket_seconds, bucket_seconds, *range_params),
        )
        summary_rows = await self.fetch_all(
            f"""
            SELECT event_type,
                   COUNT(*) AS count,
                   COUNT(DISTINCT NULLIF(uid, 0)) AS unique_users
            FROM event_time_details
            {range_where_sql}
            GROUP BY event_type
            """,
            range_params,
        )
        total_row = await self.fetch_one(
            f"""
            SELECT COUNT(*) AS total_events,
                   COUNT(DISTINCT NULLIF(uid, 0)) AS unique_users
            FROM event_time_details
            {range_where_sql}
            """,
            range_params,
        )
        gift_rows = await self.fetch_all(
            f"""
            SELECT FROM_UNIXTIME(FLOOR(UNIX_TIMESTAMP(event_at) / %s) * %s) AS bucket_at,
                   COUNT(*) AS gift_count,
                   COUNT(DISTINCT NULLIF(uid, 0)) AS gift_users,
                   COALESCE(SUM(gift_total_price), 0) AS gift_total_coin
            FROM gift_events
            {range_where_sql}
            GROUP BY bucket_at
            ORDER BY bucket_at ASC
            """,
            (bucket_seconds, bucket_seconds, *range_params),
        )
        gift_total = await self.fetch_one(
            f"""
            SELECT COALESCE(SUM(gift_total_price), 0) AS gift_total_coin
            FROM gift_events
            {range_where_sql}
            """,
            range_params,
        )
        gift_rank = await self.fetch_all(
            f"""
            SELECT uid,
                   MAX(username) AS username,
                   COUNT(*) AS gift_count,
                   COALESCE(SUM(gift_num), 0) AS gift_num,
                   COALESCE(SUM(gift_total_price), 0) AS gift_total_coin,
                   COUNT(DISTINCT gift_id) AS gift_kinds,
                   MAX(event_at) AS last_gift_at
            FROM gift_events
            {range_where_sql}
              AND gift_total_price IS NOT NULL
              AND gift_total_price > 0
            GROUP BY uid
            ORDER BY gift_total_coin DESC, gift_count DESC, last_gift_at DESC
            LIMIT 10
            """,
            range_params,
        )

        series_by_bucket: Dict[datetime, Dict[str, Any]] = {}
        cursor = start_at.replace(second=0, microsecond=0)
        cursor = cursor - timedelta(minutes=cursor.minute % bucket_minutes)
        while cursor <= end_at:
            series_by_bucket[cursor] = {
                "bucket": cursor,
                "label": cursor.strftime("%H:%M"),
                "danmaku_count": 0,
                "enter_room_count": 0,
                "enter_room_users": 0,
                "follow_count": 0,
                "follow_users": 0,
                "gift_count": 0,
                "gift_users": 0,
                "gift_total_coin": 0,
                "guard_count": 0,
                "super_chat_count": 0,
            }
            cursor += timedelta(minutes=bucket_minutes)

        for row in event_rows:
            bucket_at = row["bucket_at"]
            if bucket_at not in series_by_bucket:
                continue
            event_type = str(row["event_type"])
            count = int(row["count"] or 0)
            unique_users = int(row["unique_users"] or 0)
            if event_type == "danmaku":
                series_by_bucket[bucket_at]["danmaku_count"] = count
            elif event_type == "enter_room":
                series_by_bucket[bucket_at]["enter_room_count"] = count
                series_by_bucket[bucket_at]["enter_room_users"] = unique_users
            elif event_type == "follow":
                series_by_bucket[bucket_at]["follow_count"] = count
                series_by_bucket[bucket_at]["follow_users"] = unique_users
            elif event_type == "guard":
                series_by_bucket[bucket_at]["guard_count"] = count
            elif event_type == "super_chat":
                series_by_bucket[bucket_at]["super_chat_count"] = count

        for row in gift_rows:
            bucket_at = row["bucket_at"]
            if bucket_at not in series_by_bucket:
                continue
            series_by_bucket[bucket_at]["gift_count"] = int(row["gift_count"] or 0)
            series_by_bucket[bucket_at]["gift_users"] = int(row["gift_users"] or 0)
            series_by_bucket[bucket_at]["gift_total_coin"] = int(row["gift_total_coin"] or 0)

        summary_by_type = {
            str(row["event_type"]): {
                "count": int(row["count"] or 0),
                "unique_users": int(row["unique_users"] or 0),
            }
            for row in summary_rows
        }
        series = []
        for item in series_by_bucket.values():
            copied = dict(item)
            copied["bucket"] = item["bucket"].isoformat(timespec="seconds")
            series.append(copied)

        return {
            "range": {
                "room_id": room_id,
                "live_session_id": live_session_id,
                "session": session_row,
                "start": start_at.isoformat(timespec="seconds"),
                "end": end_at.isoformat(timespec="seconds"),
                "minutes": minutes,
                "bucket_minutes": bucket_minutes,
            },
            "summary": {
                "total_events": int((total_row or {}).get("total_events") or 0),
                "unique_users": int((total_row or {}).get("unique_users") or 0),
                "by_type": summary_by_type,
                "gift_total_coin": int((gift_total or {}).get("gift_total_coin") or 0),
            },
            "series": series,
            "gift_rank": [
                {
                    "uid": row.get("uid"),
                    "username": row.get("username") or "",
                    "gift_count": int(row.get("gift_count") or 0),
                    "gift_num": int(row.get("gift_num") or 0),
                    "gift_total_coin": int(row.get("gift_total_coin") or 0),
                    "gift_kinds": int(row.get("gift_kinds") or 0),
                    "last_gift_at": row.get("last_gift_at"),
                }
                for row in gift_rank
            ],
        }

    async def create_monitor_run(
        self,
        room_id: int,
        *,
        configured_room_id: Optional[int] = None,
        client_id: Optional[str] = None,
    ) -> int:
        if self._pool is None:
            raise RuntimeError("database is not connected")
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """
                    INSERT INTO monitor_runs
                        (room_id, configured_room_id, client_id, running, started_at, last_heartbeat_at)
                    VALUES (%s, %s, %s, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                    (room_id, configured_room_id, client_id),
                )
                return int(cursor.lastrowid)

    async def reset_running_monitor_runs(self, reason: str = "api restarted") -> None:
        await self.execute(
            """
            UPDATE room_collect_leases
            SET lease_expires_at = TIMESTAMPADD(SECOND, -1, CURRENT_TIMESTAMP),
                updated_at = CURRENT_TIMESTAMP
            """
        )
        await self.execute(
            """
            UPDATE monitor_runs
            SET running = 0,
                stopped_at = COALESCE(stopped_at, CURRENT_TIMESTAMP),
                error_message = COALESCE(error_message, %s),
                updated_at = CURRENT_TIMESTAMP
            WHERE running = 1
            """,
            (reason,),
        )

    async def reset_stale_monitor_runs(self, stale_after_seconds: int) -> None:
        await self.execute(
            """
            UPDATE room_collect_leases l
            JOIN monitor_runs r ON r.id = l.run_id
            SET l.lease_expires_at = TIMESTAMPADD(SECOND, -1, CURRENT_TIMESTAMP),
                l.updated_at = CURRENT_TIMESTAMP
            WHERE r.running = 1
              AND TIMESTAMPDIFF(SECOND, COALESCE(r.last_heartbeat_at, r.started_at), CURRENT_TIMESTAMP) > %s
            """,
            (stale_after_seconds,),
        )
        await self.execute(
            """
            UPDATE monitor_runs
            SET running = 0,
                stopped_at = COALESCE(stopped_at, CURRENT_TIMESTAMP),
                error_message = COALESCE(error_message, 'collector heartbeat timeout'),
                updated_at = CURRENT_TIMESTAMP
            WHERE running = 1
              AND TIMESTAMPDIFF(SECOND, COALESCE(last_heartbeat_at, started_at), CURRENT_TIMESTAMP) > %s
            """,
            (stale_after_seconds,),
        )

    async def running_monitor_runs(self) -> List[Dict[str, Any]]:
        return await self.fetch_all(
            """
            SELECT id, room_id, configured_room_id, client_id, started_at, last_heartbeat_at
            FROM monitor_runs
            WHERE running = 1
            ORDER BY id DESC
            """
        )

    async def active_monitor_room_ids_for_client(self, client_id: str) -> List[int]:
        rows = await self.fetch_all(
            """
            SELECT DISTINCT l.room_id
            FROM room_collect_leases l
            JOIN monitor_runs r ON r.id = l.run_id
            WHERE l.client_id = %s
              AND l.lease_expires_at > CURRENT_TIMESTAMP
              AND r.running = 1
            ORDER BY l.room_id ASC
            """,
            (client_id,),
        )
        return [int(row["room_id"]) for row in rows]

    async def get_running_monitor_run(self, room_id: int) -> Optional[Dict[str, Any]]:
        return await self.fetch_one(
            """
            SELECT id,
                   room_id,
                   configured_room_id,
                   client_id,
                   started_at,
                   last_heartbeat_at,
                   TIMESTAMPDIFF(
                       SECOND,
                       COALESCE(last_heartbeat_at, started_at),
                       CURRENT_TIMESTAMP
                   ) AS heartbeat_age_seconds
            FROM monitor_runs
            WHERE room_id = %s
              AND running = 1
            ORDER BY id DESC
            LIMIT 1
            """,
            (room_id,),
        )

    async def claim_monitor_run(
        self,
        room_id: int,
        *,
        configured_room_id: int,
        client_id: str,
        stale_after_seconds: int,
    ) -> Optional[Dict[str, Any]]:
        if self._pool is None:
            raise RuntimeError("database is not connected")

        claimed_run_id: Optional[int] = None
        reused_existing = False
        async with self._pool.acquire() as conn:
            await conn.begin()
            try:
                async with conn.cursor(aiomysql.DictCursor) as cursor:
                    await cursor.execute(
                        """
                        INSERT INTO room_collect_leases
                            (room_id, configured_room_id, client_id, run_id, lease_expires_at, last_heartbeat_at)
                        VALUES (
                            %s,
                            %s,
                            '',
                            NULL,
                            TIMESTAMPADD(SECOND, -1, CURRENT_TIMESTAMP),
                            NULL
                        )
                        ON DUPLICATE KEY UPDATE room_id = room_id
                        """,
                        (room_id, configured_room_id),
                    )
                    await cursor.execute(
                        """
                        SELECT l.room_id,
                               l.configured_room_id,
                               l.client_id,
                               l.run_id,
                               l.lease_expires_at,
                               CASE
                                   WHEN l.lease_expires_at <= CURRENT_TIMESTAMP THEN 1
                                   ELSE 0
                               END AS lease_expired,
                               r.running AS run_running
                        FROM room_collect_leases l
                        LEFT JOIN monitor_runs r ON r.id = l.run_id
                        WHERE l.room_id = %s
                        FOR UPDATE
                        """,
                        (room_id,),
                    )
                    lease = await cursor.fetchone()
                    if lease is None:
                        await conn.rollback()
                        return None

                    lease_client_id = str(lease.get("client_id") or "")
                    lease_run_id = int(lease["run_id"]) if lease.get("run_id") is not None else None
                    lease_expired = bool(int(lease.get("lease_expired") or 0))
                    run_running = bool(int(lease.get("run_running") or 0))

                    if lease_client_id == client_id and lease_run_id is not None and run_running:
                        claimed_run_id = lease_run_id
                        reused_existing = True
                        await cursor.execute(
                            """
                            UPDATE monitor_runs
                            SET last_heartbeat_at = CURRENT_TIMESTAMP,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE id = %s
                              AND client_id = %s
                              AND running = 1
                            """,
                            (claimed_run_id, client_id),
                        )
                    else:
                        owned_by_other = bool(lease_client_id) and lease_client_id != client_id
                        if owned_by_other and lease_run_id is not None and run_running and not lease_expired:
                            await conn.rollback()
                            return None

                        if lease_run_id is not None and (lease_expired or not run_running):
                            await cursor.execute(
                                """
                                UPDATE monitor_runs
                                SET running = 0,
                                    stopped_at = COALESCE(stopped_at, CURRENT_TIMESTAMP),
                                    error_message = COALESCE(error_message, 'collector heartbeat timeout'),
                                    updated_at = CURRENT_TIMESTAMP
                                WHERE id = %s
                                  AND running = 1
                                """,
                                (lease_run_id,),
                            )

                        await cursor.execute(
                            """
                            INSERT INTO monitor_runs
                                (room_id, configured_room_id, client_id, running, started_at, last_heartbeat_at)
                            VALUES (%s, %s, %s, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                            """,
                            (room_id, configured_room_id, client_id),
                        )
                        claimed_run_id = int(cursor.lastrowid)

                    await cursor.execute(
                        """
                        UPDATE room_collect_leases
                        SET configured_room_id = %s,
                            client_id = %s,
                            run_id = %s,
                            lease_expires_at = DATE_ADD(CURRENT_TIMESTAMP, INTERVAL %s SECOND),
                            last_heartbeat_at = CURRENT_TIMESTAMP,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE room_id = %s
                        """,
                        (configured_room_id, client_id, claimed_run_id, stale_after_seconds, room_id),
                    )
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise

        if claimed_run_id is None:
            return None
        run = await self.get_monitor_run(claimed_run_id)
        if run is not None:
            run["reused_existing"] = reused_existing
        return run

    async def get_monitor_run(self, run_id: int) -> Optional[Dict[str, Any]]:
        return await self.fetch_one(
            """
            SELECT id, room_id, configured_room_id, client_id, running, started_at, last_heartbeat_at
            FROM monitor_runs
            WHERE id = %s
            """,
            (run_id,),
        )

    async def heartbeat_monitor_run(self, run_id: int, *, client_id: str) -> bool:
        if self._pool is None:
            raise RuntimeError("database is not connected")
        async with self._pool.acquire() as conn:
            await conn.begin()
            try:
                async with conn.cursor() as cursor:
                    rowcount = await cursor.execute(
                        """
                        UPDATE monitor_runs
                        SET last_heartbeat_at = CURRENT_TIMESTAMP,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s
                          AND client_id = %s
                          AND running = 1
                        """,
                        (run_id, client_id),
                    )
                    if rowcount <= 0:
                        await conn.rollback()
                        return False
                    await cursor.execute(
                        """
                        UPDATE room_collect_leases
                        SET lease_expires_at = DATE_ADD(CURRENT_TIMESTAMP, INTERVAL %s SECOND),
                            last_heartbeat_at = CURRENT_TIMESTAMP,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE run_id = %s
                          AND client_id = %s
                        """,
                        (self._settings.collector_stale_seconds, run_id, client_id),
                    )
                await conn.commit()
                return True
            except Exception:
                await conn.rollback()
                raise

    async def finish_running_room_runs(self, room_id: int, reason: str) -> None:
        logger.info("finishing running monitor runs room=%s reason=%s", room_id, reason)
        await self.execute(
            """
            UPDATE room_collect_leases
            SET lease_expires_at = TIMESTAMPADD(SECOND, -1, CURRENT_TIMESTAMP),
                updated_at = CURRENT_TIMESTAMP
            WHERE room_id = %s
            """,
            (room_id,),
        )
        await self.execute(
            """
            UPDATE monitor_runs
            SET running = 0,
                stopped_at = COALESCE(stopped_at, CURRENT_TIMESTAMP),
                error_message = COALESCE(error_message, %s),
                updated_at = CURRENT_TIMESTAMP
            WHERE room_id = %s
              AND running = 1
            """,
            (reason, room_id),
        )

    async def finish_monitor_run(self, run_id: int, *, error_message: Optional[str] = None) -> None:
        logger.info("finishing monitor run=%s reason=%s", run_id, error_message)
        await self.execute(
            """
            UPDATE room_collect_leases
            SET lease_expires_at = TIMESTAMPADD(SECOND, -1, CURRENT_TIMESTAMP),
                updated_at = CURRENT_TIMESTAMP
            WHERE run_id = %s
            """,
            (run_id,),
        )
        await self.execute(
            """
            UPDATE monitor_runs
            SET running = 0,
                stopped_at = CURRENT_TIMESTAMP,
                error_message = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (error_message, run_id),
        )

    async def finish_monitor_run_by_client(
        self,
        run_id: int,
        *,
        client_id: str,
        error_message: Optional[str] = None,
    ) -> bool:
        logger.info("client requested monitor run stop run=%s client=%s reason=%s", run_id, client_id, error_message)
        rowcount = await self.execute(
            """
            UPDATE monitor_runs
            SET running = 0,
                stopped_at = CURRENT_TIMESTAMP,
                error_message = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
              AND client_id = %s
              AND running = 1
            """,
            (error_message, run_id, client_id),
        )
        if rowcount > 0:
            await self.execute(
                """
                UPDATE room_collect_leases
                SET lease_expires_at = TIMESTAMPADD(SECOND, -1, CURRENT_TIMESTAMP),
                    updated_at = CURRENT_TIMESTAMP
                WHERE run_id = %s
                  AND client_id = %s
                """,
                (run_id, client_id),
            )
        return rowcount > 0

    async def recent_monitor_runs(self, limit: int = 20) -> List[Dict[str, Any]]:
        return await self.fetch_all(
            """
            SELECT id,
                   room_id,
                   configured_room_id,
                   client_id,
                   running,
                   started_at,
                   last_heartbeat_at,
                   stopped_at,
                   error_message,
                   reconnect_count
            FROM monitor_runs
            ORDER BY id DESC
            LIMIT %s
            """,
            (limit,),
        )

    async def active_monitor_room_ids(self) -> List[int]:
        rows = await self.fetch_all(
            """
            SELECT DISTINCT l.room_id
            FROM room_collect_leases l
            JOIN monitor_runs r ON r.id = l.run_id
            WHERE r.running = 1
              AND l.lease_expires_at > CURRENT_TIMESTAMP
            ORDER BY l.room_id ASC
            """
        )
        return [int(row["room_id"]) for row in rows]
