from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List

import pytest

import blivedm_api.coordinator as coordinator_module
from blivedm_api.coordinator import RoomCoordinator


pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@dataclass
class FakeSettings:
    collector_stale_seconds: int = 90
    bilibili_heartbeat_interval_seconds: int = 30
    room_poll_interval_seconds: int = 30


@dataclass
class FakeRoomInfo:
    real_room_id: int
    title: str
    anchor_uid: int
    live_status: int
    live_time: datetime


@dataclass
class FakeCollectConfig:
    room_id: int
    anchor_uid: int
    uid: int = 0
    buvid: str = "fake-buvid"
    token: str = "fake-token"
    host_list: List[Dict[str, Any]] = None

    def __post_init__(self):
        if self.host_list is None:
            self.host_list = [{"host": "example.test", "wss_port": 443}]


class FakeDatabase:
    def __init__(
        self,
        *,
        client_enabled=True,
        max_active_rooms=50,
        owned_room_ids=None,
        denied_room_ids=None,
        reused_room_ids=None,
    ):
        self.client_enabled = client_enabled
        self.max_active_rooms = max_active_rooms
        self.owned_room_ids = set(owned_room_ids or [])
        self.denied_room_ids = set(denied_room_ids or [])
        self.reused_room_ids = set(reused_room_ids or [])
        self.touched_clients = []
        self.claimed_rooms = []
        self.finished_runs = []
        self.rooms = [
            {"room_id": 1001, "real_room_id": 1001, "room_title": "r1", "anchor_uid": 11, "enabled": 1},
            {"room_id": 1002, "real_room_id": 1002, "room_title": "r2", "anchor_uid": 22, "enabled": 1},
        ]

    async def touch_collector_client(self, client_id):
        self.touched_clients.append(client_id)

    async def get_collector_client_runtime_config(self, client_id):
        return {
            "enabled": self.client_enabled,
            "bili_sessdata": "",
            "max_active_rooms": self.max_active_rooms,
        }

    async def reset_stale_monitor_runs(self, stale_after_seconds):
        return None

    async def list_rooms(self, enabled_only=False):
        return list(self.rooms)

    async def update_room_status(self, *args, **kwargs):
        return None

    async def ensure_live_session(self, *, configured_room_id, real_room_id, room_title, anchor_uid, started_at):
        return {"id": real_room_id + 10000}

    async def finish_live_session(self, **kwargs):
        return None

    async def finish_running_room_runs(self, *args, **kwargs):
        return None

    async def touch_room_check(self, room_id):
        return None

    async def running_monitor_runs(self):
        return []

    async def active_monitor_room_ids_for_client(self, client_id):
        return sorted(self.owned_room_ids)

    async def claim_monitor_run(self, room_id, *, configured_room_id, client_id, stale_after_seconds):
        self.claimed_rooms.append(room_id)
        if room_id in self.denied_room_ids:
            return None
        return {
            "id": room_id + 100,
            "room_id": room_id,
            "configured_room_id": configured_room_id,
            "client_id": client_id,
            "running": 1,
            "reused_existing": room_id in self.reused_room_ids,
        }

    async def finish_monitor_run(self, run_id, *, error_message=None):
        self.finished_runs.append((run_id, error_message))


async def install_bili_fakes(monkeypatch):
    async def fake_fetch_room_info(session, room_id):
        return FakeRoomInfo(
            real_room_id=room_id,
            title=f"room-{room_id}",
            anchor_uid=room_id + 10,
            live_status=1,
            live_time=datetime(2026, 6, 4, 10, 0, 0),
        )

    async def fake_fetch_collect_config(session, room_id):
        return FakeCollectConfig(room_id=room_id, anchor_uid=room_id + 10)

    monkeypatch.setattr(coordinator_module, "fetch_room_info", fake_fetch_room_info)
    monkeypatch.setattr(coordinator_module, "fetch_collect_config", fake_fetch_collect_config)


async def build_coordinator(monkeypatch, db):
    await install_bili_fakes(monkeypatch)
    coordinator = RoomCoordinator(FakeSettings(), db)
    coordinator._session = object()
    return coordinator


async def test_disabled_collector_gets_no_tasks(monkeypatch):
    db = FakeDatabase(client_enabled=False)
    coordinator = await build_coordinator(monkeypatch, db)

    tasks = await coordinator.claim_tasks(client_id="client-a", limit=50)

    assert tasks.tasks == []
    assert tasks.keep_run_ids == []
    assert db.touched_clients == ["client-a"]
    assert db.claimed_rooms == []


async def test_collector_max_active_rooms_limits_task_count(monkeypatch):
    db = FakeDatabase(max_active_rooms=1)
    coordinator = await build_coordinator(monkeypatch, db)

    tasks = await coordinator.claim_tasks(client_id="client-a", limit=50)

    assert [task["room_id"] for task in tasks.tasks] == [1001]
    assert tasks.keep_run_ids == []
    assert db.claimed_rooms == [1001]


async def test_owned_rooms_are_prioritized_when_capacity_is_limited(monkeypatch):
    db = FakeDatabase(max_active_rooms=1, owned_room_ids={1002})
    coordinator = await build_coordinator(monkeypatch, db)

    tasks = await coordinator.claim_tasks(client_id="client-a", limit=50)

    assert [task["room_id"] for task in tasks.tasks] == [1002]
    assert tasks.keep_run_ids == []
    assert db.claimed_rooms == [1002]


async def test_room_with_active_foreign_lease_is_not_returned(monkeypatch):
    db = FakeDatabase(denied_room_ids={1001})
    coordinator = await build_coordinator(monkeypatch, db)

    tasks = await coordinator.claim_tasks(client_id="client-a", limit=50)

    assert [task["room_id"] for task in tasks.tasks] == [1002]
    assert tasks.keep_run_ids == []
    assert db.claimed_rooms == [1001, 1002]


async def test_new_run_collect_config_failure_finishes_run(monkeypatch):
    db = FakeDatabase()
    coordinator = await build_coordinator(monkeypatch, db)

    async def fake_fetch_collect_config(session, room_id):
        if room_id == 1001:
            raise RuntimeError("temporary -352")
        return FakeCollectConfig(room_id=room_id, anchor_uid=room_id + 10)

    monkeypatch.setattr(coordinator_module, "fetch_collect_config", fake_fetch_collect_config)

    tasks = await coordinator.claim_tasks(client_id="client-a", limit=50)

    assert [task["room_id"] for task in tasks.tasks] == [1002]
    assert tasks.keep_run_ids == []
    # 重试耗尽后才结束 run；错误信息不再内联底层异常文本。
    assert db.finished_runs == [(1101, "collect config failed for room=1001")]


async def test_reused_run_skips_collect_config_fetch(monkeypatch):
    # 复用中的 run 不应再向 B 站拉弹幕配置，只进入 keep_run_ids；
    # 这是消除“监听不停断开”抖动的关键（避免对 getDanmuInfo 的高频调用）。
    db = FakeDatabase(reused_room_ids={1001})
    coordinator = await build_coordinator(monkeypatch, db)

    fetched_rooms = []

    async def fake_fetch_collect_config(session, room_id):
        fetched_rooms.append(room_id)
        return FakeCollectConfig(room_id=room_id, anchor_uid=room_id + 10)

    monkeypatch.setattr(coordinator_module, "fetch_collect_config", fake_fetch_collect_config)

    tasks = await coordinator.claim_tasks(client_id="client-a", limit=50)

    # 复用 run(1001) 进 keep，不进 tasks，也不触发配置拉取；新 run(1002) 正常拉取。
    assert [task["room_id"] for task in tasks.tasks] == [1002]
    assert tasks.keep_run_ids == [1101]
    assert db.finished_runs == []
    assert fetched_rooms == [1002]
