from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import Settings
from .coordinator import RoomCoordinator
from .db import Database
from .events import parse_event_or_error
from .room_parser import parse_room_id

STATIC_DIR = Path(__file__).resolve().parent / "static"
logger = logging.getLogger(__name__)


class RoomCreate(BaseModel):
    room: str = Field(..., min_length=1)
    enabled: bool = True
    remark: str = ""


class RoomPatch(BaseModel):
    enabled: Optional[bool] = None
    remark: Optional[str] = None


class RawEventPayload(BaseModel):
    run_id: Optional[int] = None
    room_id: int
    cmd: str = ""
    raw: Dict[str, Any]
    received_at: Optional[str] = None


class EventBatchPayload(BaseModel):
    source: str = "collector-client"
    client_id: Optional[str] = None
    received_at: Optional[str] = None
    events: List[RawEventPayload]


class CollectorRunPayload(BaseModel):
    client_id: str = Field(..., min_length=1, max_length=128)
    reason: Optional[str] = None


def parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid datetime: {value}") from exc


@asynccontextmanager
async def lifespan(app: FastAPI):
    # API 进程持有数据库连接池和房间调度器；采集客户端独立运行，
    # 只通过内部 HTTP 接口和 API 进程通信。
    settings = Settings.from_env()
    settings.validate()
    db = Database(settings)
    await db.connect()
    await db.migrate()
    reclassified = await db.reclassify_unknown_events()
    if reclassified:
        logger.info("reclassified %s unknown room events", reclassified)
    coordinator = RoomCoordinator(settings, db)
    await coordinator.start()

    app.state.settings = settings
    app.state.db = db
    app.state.coordinator = coordinator
    try:
        yield
    finally:
        await coordinator.stop()
        await db.close()


app = FastAPI(title="blivedm Monitor", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_coordinator(request: Request) -> RoomCoordinator:
    return request.app.state.coordinator


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/rooms")
async def list_rooms(request: Request):
    db = get_db(request)
    rooms = await db.list_rooms()
    active = set(await db.active_monitor_room_ids())
    for room in rooms:
        room["listening"] = int(room.get("real_room_id") or room["room_id"]) in active
    return {"items": rooms}


@app.post("/api/rooms", status_code=201)
async def create_room(payload: RoomCreate, request: Request):
    try:
        room_id = parse_room_id(payload.room)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    db = get_db(request)
    room = await db.upsert_room(room_id, enabled=payload.enabled, remark=payload.remark)
    get_coordinator(request).wake()
    room["listening"] = False
    return room


@app.patch("/api/rooms/{room_pk}")
async def update_room(room_pk: int, payload: RoomPatch, request: Request):
    db = get_db(request)
    old_room = await db.get_room(room_pk)
    if old_room is None:
        raise HTTPException(status_code=404, detail="room not found")
    room = await db.update_room(room_pk, enabled=payload.enabled, remark=payload.remark)
    if room is None:
        raise HTTPException(status_code=404, detail="room not found")
    if payload.enabled is False:
        await db.finish_live_session(
            configured_room_id=int(old_room["room_id"]),
            real_room_id=int(old_room.get("real_room_id") or old_room["room_id"]),
        )
    get_coordinator(request).wake()
    active = set(await db.active_monitor_room_ids())
    room["listening"] = int(room.get("real_room_id") or room["room_id"]) in active
    return room


@app.delete("/api/rooms/{room_pk}")
async def delete_room(room_pk: int, request: Request):
    db = get_db(request)
    room = await db.get_room(room_pk)
    if room is None:
        raise HTTPException(status_code=404, detail="room not found")
    await db.finish_live_session(
        configured_room_id=int(room["room_id"]),
        real_room_id=int(room.get("real_room_id") or room["room_id"]),
    )
    deleted = await db.delete_room(room_pk)
    get_coordinator(request).wake()
    return {"deleted": deleted}


@app.get("/api/events")
async def list_events(
    request: Request,
    room_id: Optional[int] = None,
    event_type: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    before_id: Optional[int] = Query(None, ge=1),
    live_session_id: Optional[int] = Query(None, ge=1),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    db = get_db(request)
    events = await db.list_events(
        room_id=room_id,
        event_type=event_type,
        start=parse_datetime(start),
        end=parse_datetime(end),
        before_id=before_id,
        live_session_id=live_session_id,
        limit=limit + 1,
        offset=offset,
    )
    items = events[:limit]
    return {
        "items": items,
        "has_more": len(events) > limit,
        "next_before_id": min((int(item["id"]) for item in items), default=None),
    }


@app.get("/api/live-sessions")
async def list_live_sessions(
    request: Request,
    room_id: Optional[int] = None,
    status: Optional[str] = Query(None, pattern="^(live|ended)$"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    sessions = await get_db(request).list_live_sessions(
        room_id=room_id,
        status=status,
        limit=limit + 1,
        offset=offset,
    )
    items = sessions[:limit]
    return {
        "items": items,
        "has_more": len(sessions) > limit,
    }


@app.post("/internal/events/batch")
async def collect_event_batch(payload: EventBatchPayload, request: Request):
    # 采集端只回传 B 站原始命令。解析集中在 API 侧，后续修改
    # 入库规则时不需要重新部署所有采集端。
    db = get_db(request)
    persisted = 0
    skipped = 0
    for item in payload.events:
        command = dict(item.raw)
        if item.cmd and "cmd" not in command:
            command["cmd"] = item.cmd
        event = parse_event_or_error(item.room_id, command)
        if event is None:
            skipped += 1
            continue
        await db.insert_event(event)
        persisted += 1
    return {"received": len(payload.events), "persisted": persisted, "skipped": skipped}


@app.get("/internal/collector/tasks")
async def collect_tasks(
    request: Request,
    client_id: str = Query(..., min_length=1, max_length=128),
    limit: int = Query(50, ge=1, le=200),
):
    # 任务认领同时也是租约机制：一个直播间同一时间只分配给一个
    # 新鲜的 collector run，后续靠心跳避免被判定为过期。
    tasks = await get_coordinator(request).claim_tasks(client_id=client_id, limit=limit)
    return {
        "client_id": client_id,
        "poll_interval_seconds": request.app.state.settings.collector_task_poll_interval_seconds,
        "items": tasks,
    }


@app.post("/internal/collector/runs/{run_id}/heartbeat")
async def collector_run_heartbeat(run_id: int, payload: CollectorRunPayload, request: Request):
    ok = await get_db(request).heartbeat_monitor_run(run_id, client_id=payload.client_id)
    if not ok:
        raise HTTPException(status_code=404, detail="running collector run not found")
    return {"ok": True}


@app.post("/internal/collector/runs/{run_id}/stop")
async def collector_run_stop(run_id: int, payload: CollectorRunPayload, request: Request):
    ok = await get_db(request).finish_monitor_run_by_client(
        run_id,
        client_id=payload.client_id,
        error_message=payload.reason or "collector stopped",
    )
    return {"ok": ok}


@app.get("/api/status")
async def status(request: Request):
    db = get_db(request)
    active_rooms = await db.active_monitor_room_ids()
    return {
        "running": True,
        "mode": "api-coordinator",
        "database": "connected",
        "coordinator_running": get_coordinator(request).is_running,
        "poll_interval_seconds": request.app.state.settings.room_poll_interval_seconds,
        "collector_stale_seconds": request.app.state.settings.collector_stale_seconds,
        "active_rooms": active_rooms,
        "recent_runs": await db.recent_monitor_runs(),
        "rooms_count": len(await db.list_rooms()),
    }


def main() -> None:
    settings = Settings.from_env()
    uvicorn.run("blivedm_api.main:app", host=settings.app_host, port=settings.app_port, reload=False)


if __name__ == "__main__":
    main()
