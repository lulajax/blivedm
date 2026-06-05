"""一次性清理重复直播场次（同一 room_id + started_at 被拆成多条）。

成因见 db.ensure_live_session 的修复：旧逻辑在采集中断/短暂判定下播后，会为
同一场直播（开播时间不变）新建一条场次。本脚本把每组重复场次合并为一条 keeper
（优先仍在直播的那条，否则取最新 id），将所有引用 live_session_id 的明细表
重指向 keeper，重算 keeper 的起止时间后删除多余场次。幂等：可重复执行。

用法（在 api/ 目录下）：
    uv run --project . python scripts/dedupe_live_sessions.py          # 预演，只打印不改库
    uv run --project . python scripts/dedupe_live_sessions.py --apply  # 实际执行
"""
from __future__ import annotations

import asyncio
import os
import sys

# 允许从 api/scripts 直接运行：把 api/ 加入 import 路径。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiomysql

from blivedm_api.config import Settings
from blivedm_api.db import Database

# 所有带 live_session_id 外键语义的表，合并时一并重指向 keeper。
REF_TABLES = [
    "room_events",
    "event_time_details",
    "danmaku_events",
    "enter_room_events",
    "follow_events",
    "gift_events",
    "guard_events",
    "super_chat_events",
]


async def main(apply: bool) -> None:
    settings = Settings.from_env()
    settings.validate()
    db = Database(settings)
    await db.connect()
    try:
        groups = await db.fetch_all(
            """
            SELECT room_id, started_at, COUNT(*) AS n
            FROM live_sessions
            GROUP BY room_id, started_at
            HAVING COUNT(*) > 1
            ORDER BY room_id, started_at
            """
        )
        if not groups:
            print("no duplicate live_sessions found")
            return

        planned = 0
        for g in groups:
            room_id = int(g["room_id"])
            started_at = g["started_at"]
            rows = await db.fetch_all(
                """
                SELECT id, status
                FROM live_sessions
                WHERE room_id = %s AND started_at = %s
                ORDER BY (status = 'live') DESC, id DESC
                """,
                (room_id, started_at),
            )
            keeper_id = int(rows[0]["id"])
            dup_ids = [int(r["id"]) for r in rows[1:]]
            planned += len(dup_ids)
            print(f"room {room_id} @ {started_at}: keep #{keeper_id}, merge {dup_ids}")
            if not apply:
                continue

            assert db._pool is not None  # noqa: SLF001 - 维护脚本直接用连接池开事务
            async with db._pool.acquire() as conn:  # noqa: SLF001
                await conn.begin()
                try:
                    in_clause = ",".join(["%s"] * len(dup_ids))
                    async with conn.cursor(aiomysql.DictCursor) as cur:
                        for table in REF_TABLES:
                            await cur.execute(
                                f"UPDATE {table} SET live_session_id = %s "
                                f"WHERE live_session_id IN ({in_clause})",
                                (keeper_id, *dup_ids),
                            )
                        await cur.execute(
                            """
                            SELECT MIN(detected_started_at) AS dstart,
                                   MAX(ended_at) AS ended,
                                   MAX(detected_ended_at) AS dended,
                                   MAX(status = 'live') AS has_live
                            FROM live_sessions
                            WHERE room_id = %s AND started_at = %s
                            """,
                            (room_id, started_at),
                        )
                        agg = await cur.fetchone()
                        if bool(int(agg["has_live"] or 0)):
                            await cur.execute(
                                """
                                UPDATE live_sessions
                                SET status = 'live', ended_at = NULL, detected_ended_at = NULL,
                                    detected_started_at = COALESCE(%s, detected_started_at),
                                    updated_at = CURRENT_TIMESTAMP
                                WHERE id = %s
                                """,
                                (agg["dstart"], keeper_id),
                            )
                        else:
                            await cur.execute(
                                """
                                UPDATE live_sessions
                                SET status = 'ended', ended_at = %s, detected_ended_at = %s,
                                    detected_started_at = COALESCE(%s, detected_started_at),
                                    updated_at = CURRENT_TIMESTAMP
                                WHERE id = %s
                                """,
                                (agg["ended"], agg["dended"], agg["dstart"], keeper_id),
                            )
                        await cur.execute(
                            f"UPDATE rooms SET current_session_id = %s, updated_at = CURRENT_TIMESTAMP "
                            f"WHERE current_session_id IN ({in_clause})",
                            (keeper_id, *dup_ids),
                        )
                        await cur.execute(
                            f"DELETE FROM live_sessions WHERE id IN ({in_clause})",
                            (*dup_ids,),
                        )
                    await conn.commit()
                except Exception:
                    await conn.rollback()
                    raise

        verb = "removed" if apply else "would remove"
        print(f"{verb} {planned} duplicate session rows across {len(groups)} broadcast group(s)")
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main(apply="--apply" in sys.argv))
