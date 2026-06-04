import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

import pytest

from blivedm_api.event_projector import EventProjector, build_projection_rows


pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@dataclass(frozen=True)
class FakeSettings:
    event_projector_batch_size: int = 1000
    event_projector_poll_interval_seconds: float = 2
    event_projector_timezone: str = "Asia/Shanghai"


class FakeDatabase:
    def __init__(self, rows: List[Dict[str, Any]]):
        self.rows = rows
        self.last_event_id = 0
        self.updated_last_event_id = None
        self.fact_rows = {}
        self.time_rows = {}
        self.resolved_contexts = []

    async def get_projection_last_event_id(self, name: str) -> int:
        return self.last_event_id

    async def fetch_room_events_after(self, last_event_id: int, limit: int):
        return [row for row in self.rows if int(row["id"]) > last_event_id][:limit]

    async def resolve_event_projection_context(self, *, room_id: int, event_at: datetime, live_session_id: Optional[int]):
        self.resolved_contexts.append((room_id, event_at, live_session_id))
        return {
            "configured_room_id": 1000,
            "live_session_id": live_session_id or 99,
            "anchor_uid": 7001,
            "session_started_at": datetime(2026, 6, 4, 10, 0, 0),
        }

    async def upsert_analytic_event(self, table_name: str, row: Dict[str, Any]) -> None:
        self.fact_rows[int(row["event_id"])] = (table_name, row)

    async def upsert_analytic_events(self, table_name: str, rows: List[Dict[str, Any]]) -> None:
        for row in rows:
            await self.upsert_analytic_event(table_name, row)

    async def upsert_event_time_detail(self, row: Dict[str, Any]) -> None:
        self.time_rows[int(row["event_id"])] = row

    async def upsert_event_time_details(self, rows: List[Dict[str, Any]]) -> None:
        for row in rows:
            await self.upsert_event_time_detail(row)

    async def update_projection_last_event_id(self, name: str, last_event_id: int) -> None:
        self.updated_last_event_id = last_event_id
        self.last_event_id = last_event_id


def test_build_gift_projection_contains_time_session_user_and_anchor_dimensions():
    event = {
        "id": 12,
        "room_id": 2001,
        "event_type": "gift",
        "live_session_id": 9,
        "uid": 42,
        "username": "alice",
        "content": "辣条 x3",
        "raw_json": json.dumps(
            {
                "cmd": "SEND_GIFT",
                "data": {
                    "gift_id": 1,
                    "gift_name": "辣条",
                    "num": 3,
                    "price": 100,
                    "coin_type": "gold",
                    "batch_combo_id": "batch-1",
                    "sender_uinfo": {
                        "wealth": {"level": 8},
                        "medal": {"name": "粉丝牌", "level": 6, "anchor_uid": 7001, "guard_level": 3},
                    },
                },
            },
            ensure_ascii=False,
        ),
        "event_time": datetime(2026, 6, 4, 10, 5, 6),
        "created_at": datetime(2026, 6, 4, 10, 5, 8),
    }
    context = {
        "configured_room_id": 1000,
        "live_session_id": 9,
        "anchor_uid": 7001,
        "session_started_at": datetime(2026, 6, 4, 10, 0, 0),
    }

    projection = build_projection_rows(event, context)

    assert projection is not None
    assert projection.table_name == "gift_events"
    assert projection.fact_row["event_id"] == 12
    assert projection.fact_row["anchor_uid"] == 7001
    assert projection.fact_row["gift_id"] == 1
    assert projection.fact_row["gift_num"] == 3
    assert projection.fact_row["gift_total_price"] == 300
    assert projection.fact_row["wealth_level"] == 8
    assert projection.fact_row["medal_name"] == "粉丝牌"
    assert projection.fact_row["guard_level"] == 3
    assert projection.time_row["date_key"] == 20260604
    assert projection.time_row["hour_key"] == 2026060410
    assert projection.time_row["minute_key"] == 202606041005
    assert projection.time_row["weekday"] == 4
    assert projection.time_row["is_weekend"] == 0
    assert projection.time_row["session_elapsed_seconds"] == 306
    assert projection.time_row["ingest_delay_seconds"] == 2


async def test_projector_advances_state_over_skipped_events():
    rows = [
        {
            "id": 1,
            "room_id": 2001,
            "event_type": "ignored",
            "live_session_id": None,
            "uid": None,
            "username": "",
            "content": "",
            "raw_json": "{}",
            "event_time": datetime(2026, 6, 4, 10, 0, 0),
            "created_at": datetime(2026, 6, 4, 10, 0, 1),
        },
        {
            "id": 2,
            "room_id": 2001,
            "event_type": "enter_room",
            "live_session_id": None,
            "uid": 42,
            "username": "alice",
            "content": "进入房间",
            "raw_json": json.dumps({"cmd": "INTERACT_WORD", "data": {"wealth_level": 2}}, ensure_ascii=False),
            "event_time": datetime(2026, 6, 4, 10, 0, 2),
            "created_at": datetime(2026, 6, 4, 10, 0, 3),
        },
    ]
    db = FakeDatabase(rows)

    stats = await EventProjector(FakeSettings(), db).process_once()

    assert stats.read_count == 2
    assert stats.projected_count == 1
    assert stats.skipped_count == 1
    assert db.updated_last_event_id == 2
    assert db.fact_rows[2][0] == "enter_room_events"
    assert db.time_rows[2]["anchor_uid"] == 7001
    assert db.resolved_contexts == [(2001, datetime(2026, 6, 4, 10, 0, 2), None)]
