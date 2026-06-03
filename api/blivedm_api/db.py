from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

import aiomysql

from .config import Settings


CREATE_ROOMS_SQL = """
CREATE TABLE IF NOT EXISTS rooms (
    id BIGINT NOT NULL AUTO_INCREMENT,
    room_id BIGINT NOT NULL,
    real_room_id BIGINT NULL,
    room_title VARCHAR(255) NOT NULL DEFAULT '',
    anchor_uid BIGINT NULL,
    enabled TINYINT(1) NOT NULL DEFAULT 1,
    live_status TINYINT NOT NULL DEFAULT 0,
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
    uid BIGINT NULL,
    username VARCHAR(255) NOT NULL DEFAULT '',
    content TEXT NULL,
    raw_json LONGTEXT NOT NULL,
    event_time DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uniq_room_event_key (room_id, event_type, event_key),
    KEY idx_room_events_room_created (room_id, created_at),
    KEY idx_room_events_type_created (event_type, created_at)
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
        await self.execute(CREATE_ROOMS_SQL)
        await self.execute(CREATE_ROOM_EVENTS_SQL)
        await self.execute(CREATE_MONITOR_RUNS_SQL)
        await self.ensure_column("monitor_runs", "configured_room_id", "BIGINT NULL AFTER room_id")
        await self.ensure_column("monitor_runs", "client_id", "VARCHAR(128) NULL AFTER configured_room_id")
        await self.ensure_column("monitor_runs", "last_heartbeat_at", "DATETIME NULL AFTER started_at")
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

    async def execute(self, sql: str, params: Optional[Iterable[Any]] = None) -> int:
        if self._pool is None:
            raise RuntimeError("database is not connected")
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(sql, params)
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
        if enabled_only:
            return await self.fetch_all("SELECT * FROM rooms WHERE enabled = 1 ORDER BY id ASC")
        return await self.fetch_all("SELECT * FROM rooms ORDER BY id ASC")

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
    ) -> None:
        await self.execute(
            """
            UPDATE rooms
            SET real_room_id = %s,
                room_title = %s,
                anchor_uid = %s,
                live_status = %s,
                last_live_at = CASE
                    WHEN %s = 1 AND live_status <> 1 THEN CURRENT_TIMESTAMP
                    ELSE last_live_at
                END,
                last_checked_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE room_id = %s
            """,
            (real_room_id, room_title, anchor_uid, live_status, live_status, room_id),
        )

    async def touch_room_check(self, room_id: int) -> None:
        await self.execute(
            "UPDATE rooms SET last_checked_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE room_id = %s",
            (room_id,),
        )

    async def insert_event(self, event: EventRecord) -> None:
        # event_key 允许为空，因此 unknown/parse_error 会完整保留；
        # 已知业务事件使用唯一 key，避免重连或重复上报导致重复入库。
        await self.execute(
            """
            INSERT INTO room_events
                (room_id, event_type, event_key, uid, username, content, raw_json, event_time)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE id = id
            """,
            (
                event.room_id,
                event.event_type,
                event.event_key,
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
        limit: int,
        offset: int,
    ) -> List[Dict[str, Any]]:
        filters: List[str] = []
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
        where_sql = f"WHERE {' AND '.join(filters)}" if filters else ""
        params.extend([limit, offset])
        return await self.fetch_all(
            f"""
            SELECT id, room_id, event_type, event_key, uid, username, content, raw_json, event_time, created_at
            FROM room_events
            {where_sql}
            ORDER BY id DESC
            LIMIT %s OFFSET %s
            """,
            params,
        )

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
        # 一个真实直播间只允许有一个活跃 run。同一个 client 会续租，
        # 其他 client 需要等待；过期租约会先关闭再重新认领。
        active = await self.get_running_monitor_run(room_id)
        if active is not None:
            active_client_id = str(active.get("client_id") or "")
            heartbeat_age = int(active.get("heartbeat_age_seconds") or 0)
            if active_client_id == client_id:
                await self.heartbeat_monitor_run(int(active["id"]), client_id=client_id)
                return await self.get_monitor_run(int(active["id"]))
            if heartbeat_age <= stale_after_seconds:
                return None
            await self.finish_monitor_run(int(active["id"]), error_message="collector heartbeat timeout")

        run_id = await self.create_monitor_run(
            room_id,
            configured_room_id=configured_room_id,
            client_id=client_id,
        )
        return await self.get_monitor_run(run_id)

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
        rowcount = await self.execute(
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
        if rowcount > 0:
            return True
        row = await self.fetch_one(
            """
            SELECT id
            FROM monitor_runs
            WHERE id = %s
              AND client_id = %s
              AND running = 1
            """,
            (run_id, client_id),
        )
        return row is not None

    async def finish_running_room_runs(self, room_id: int, reason: str) -> None:
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
            SELECT DISTINCT room_id
            FROM monitor_runs
            WHERE running = 1
            ORDER BY room_id ASC
            """
        )
        return [int(row["room_id"]) for row in rows]
