from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import aiohttp

from .bili_api import apply_bili_cookie, fetch_collect_config, fetch_room_info
from .config import Settings
from .db import Database

logger = logging.getLogger(__name__)


class RoomCoordinator:
    """把后台配置的直播间转换成采集任务。

    这个类持有 B 站 HTTP/cookie 状态。采集客户端只接收已解析好的
    WebSocket 连接参数，不需要 B 站 HTTP 凭据。
    """

    def __init__(self, settings: Settings, db: Database):
        self._settings = settings
        self._db = db
        self._session: Optional[aiohttp.ClientSession] = None
        self._task: Optional[asyncio.Task] = None
        self._wake_event = asyncio.Event()
        self._stopping = False

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.is_running:
            return
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        apply_bili_cookie(self._session, self._settings.bili_sessdata)
        self._stopping = False
        self._task = asyncio.create_task(self._loop(), name="room-coordinator-loop")

    async def stop(self) -> None:
        self._stopping = True
        self._wake_event.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._session is not None:
            await self._session.close()
            self._session = None

    def wake(self) -> None:
        self._wake_event.set()

    async def _loop(self) -> None:
        logger.info("room coordinator loop started")
        while not self._stopping:
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("room coordinator loop failed")

            self._wake_event.clear()
            try:
                await asyncio.wait_for(
                    self._wake_event.wait(),
                    timeout=self._settings.room_poll_interval_seconds,
                )
            except asyncio.TimeoutError:
                pass
        logger.info("room coordinator loop stopped")

    async def poll_once(self) -> List[Dict[str, Any]]:
        if self._session is None:
            raise RuntimeError("coordinator session is not initialized")

        # collector 可能异常退出且来不及上报 stop。轮询前先关闭过期 run，
        # 这样其他采集端才能重新认领对应直播间。
        await self._db.reset_stale_monitor_runs(self._settings.collector_stale_seconds)
        # 这里必须查询全部 enabled 房间，而不是只查 live_status=1 的房间。
        # live_status 是上一次轮询的缓存值，只有逐个请求 B 站房间信息后，
        # 才能知道当前哪些房间已经开播或下播。
        rooms = await self._db.list_rooms(enabled_only=True)
        enabled_room_ids = {int(room["room_id"]) for room in rooms}
        confirmed_live_rooms: List[Dict[str, Any]] = []

        for room in rooms:
            room_id = int(room["room_id"])
            try:
                info = await fetch_room_info(self._session, room_id)
                await self._db.update_room_status(
                    room_id,
                    real_room_id=info.real_room_id,
                    room_title=info.title,
                    anchor_uid=info.anchor_uid,
                    live_status=info.live_status,
                    live_started_at=info.live_time,
                )
                if info.live_status != 1:
                    await self._db.finish_live_session(
                        configured_room_id=room_id,
                        real_room_id=info.real_room_id,
                    )
                    await self._db.finish_running_room_runs(info.real_room_id, "room offline")
                else:
                    session = await self._db.ensure_live_session(
                        configured_room_id=room_id,
                        real_room_id=info.real_room_id,
                        room_title=info.title,
                        anchor_uid=info.anchor_uid,
                        started_at=info.live_time or datetime.now(),
                    )
                    live_room = dict(room)
                    live_room.update(
                        {
                            "real_room_id": info.real_room_id,
                            "room_title": info.title,
                            "anchor_uid": info.anchor_uid,
                            "live_status": info.live_status,
                            "current_session_id": int(session["id"]),
                        }
                    )
                    confirmed_live_rooms.append(live_room)
            except Exception as exc:  # noqa: BLE001
                logger.warning("failed to refresh room=%s: %s", room_id, exc)
                await self._db.touch_room_check(room_id)

        for run in await self._db.running_monitor_runs():
            configured_room_id = int(run.get("configured_room_id") or run["room_id"])
            if configured_room_id not in enabled_room_ids:
                await self._db.finish_live_session(
                    configured_room_id=configured_room_id,
                    real_room_id=int(run["room_id"]),
                )
                await self._db.finish_monitor_run(int(run["id"]), error_message="room disabled")

        return confirmed_live_rooms

    async def claim_tasks(self, client_id: str, limit: int) -> List[Dict[str, Any]]:
        if self._session is None:
            raise RuntimeError("coordinator session is not initialized")

        # 任务认领是按需触发的：先刷新所有 enabled 房间的直播状态，
        # 再只使用本轮成功确认 live_status=1 的房间生成任务，避免旧缓存误下发。
        live_rooms = await self.poll_once()
        tasks: List[Dict[str, Any]] = []
        for room in live_rooms:
            if len(tasks) >= limit:
                break

            configured_room_id = int(room["room_id"])
            real_room_id = int(room.get("real_room_id") or configured_room_id)
            run = await self._db.claim_monitor_run(
                real_room_id,
                configured_room_id=configured_room_id,
                client_id=client_id,
                stale_after_seconds=self._settings.collector_stale_seconds,
            )
            if run is None:
                continue

            try:
                config = await fetch_collect_config(self._session, real_room_id)
            except Exception as exc:  # noqa: BLE001
                await self._db.finish_monitor_run(int(run["id"]), error_message=f"collect config failed: {exc}")
                logger.warning("failed to create collect config room=%s client=%s: %s", real_room_id, client_id, exc)
                continue
            tasks.append(
                {
                    "run_id": int(run["id"]),
                    "room_id": config.room_id,
                    "configured_room_id": configured_room_id,
                    "room_title": str(room.get("room_title") or ""),
                    "anchor_uid": config.anchor_uid,
                    "uid": config.uid,
                    "buvid": config.buvid,
                    "token": config.token,
                    "host_list": config.host_list,
                    "heartbeat_interval": self._settings.bilibili_heartbeat_interval_seconds,
                    "issued_at": datetime.now().isoformat(timespec="seconds"),
                }
            )

        return tasks
