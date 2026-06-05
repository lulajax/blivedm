from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

import aiohttp

from .bili_api import apply_bili_cookie, fetch_collect_config, fetch_room_info
from .config import Settings
from .db import Database

logger = logging.getLogger(__name__)

# 直播状态短 TTL 缓存：claim_tasks 每 ~10s 触发一次，缓存可避免每次都对
# 所有 enabled 房间全量请求 B 站房间信息（高频请求是 B 站风控的主要来源）。
_LIVE_STATUS_CACHE_SECONDS = 15
# 新建 run 拉弹幕配置的重试次数与退避（秒），抵御 B 站瞬时风控/抖动。
_COLLECT_CONFIG_ATTEMPTS = 3
_COLLECT_CONFIG_BACKOFF_SECONDS = 0.6


@dataclass(frozen=True)
class ClaimedTaskResult:
    tasks: List[Dict[str, Any]]
    keep_run_ids: List[int]


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
        self._live_status_cache: Optional[List[Dict[str, Any]]] = None
        self._live_status_cache_at: Optional[datetime] = None

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.is_running:
            return
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
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
                    fans_count=info.fans_count,
                    online_count=info.online_count,
                    area_name=info.area_name,
                    parent_area_name=info.parent_area_name,
                    description=info.description,
                    cover_url=info.cover_url,
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

        self._live_status_cache = confirmed_live_rooms
        self._live_status_cache_at = datetime.now()
        return confirmed_live_rooms

    async def _live_rooms_cached(self) -> List[Dict[str, Any]]:
        # claim_tasks 高频触发，这里用短 TTL 缓存复用最近一次 poll_once 的
        # 直播间快照；后台 _loop 仍按 room_poll_interval_seconds 刷新缓存。
        now = datetime.now()
        if (
            self._live_status_cache is not None
            and self._live_status_cache_at is not None
            and (now - self._live_status_cache_at).total_seconds() < _LIVE_STATUS_CACHE_SECONDS
        ):
            return self._live_status_cache
        return await self.poll_once()

    async def _fetch_collect_config_with_retry(self, session: aiohttp.ClientSession, room_id: int):
        last_exc: Optional[Exception] = None
        for attempt in range(_COLLECT_CONFIG_ATTEMPTS):
            try:
                return await fetch_collect_config(session, room_id)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt + 1 < _COLLECT_CONFIG_ATTEMPTS:
                    await asyncio.sleep(_COLLECT_CONFIG_BACKOFF_SECONDS * (attempt + 1))
        logger.warning(
            "collect config failed room=%s after %s attempts: %s",
            room_id,
            _COLLECT_CONFIG_ATTEMPTS,
            last_exc,
        )
        return None

    async def claim_tasks(self, client_id: str, limit: int) -> ClaimedTaskResult:
        if self._session is None:
            raise RuntimeError("coordinator session is not initialized")

        await self._db.touch_collector_client(client_id)
        client_config = await self._db.get_collector_client_runtime_config(client_id)
        if not client_config["enabled"]:
            return ClaimedTaskResult(tasks=[], keep_run_ids=[])
        client_sessdata = str(client_config["bili_sessdata"] or "")
        max_active_rooms = max(1, int(client_config["max_active_rooms"] or 50))
        effective_limit = min(limit, max_active_rooms)
        # 让 poll_once 的房间信息(get_info)请求也带上采集端的 SESSDATA，
        # 降低 B 站风控概率。
        apply_bili_cookie(self._session, client_sessdata)
        # 任务认领按需触发：直播状态用短 TTL 缓存复用，避免每次都全量请求
        # B 站。复制一份再排序，避免改动被多个 client 共享的缓存。
        live_rooms = list(await self._live_rooms_cached())
        owned_room_ids = set(await self._db.active_monitor_room_ids_for_client(client_id))
        live_rooms.sort(
            key=lambda item: (
                0 if int(item.get("real_room_id") or item["room_id"]) in owned_room_ids else 1,
                int(item["room_id"]),
            )
        )
        tasks: List[Dict[str, Any]] = []
        keep_run_ids: List[int] = []
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as collect_session:
            apply_bili_cookie(collect_session, client_sessdata)
            for room in live_rooms:
                if len(tasks) >= effective_limit:
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

                run_id = int(run["id"])
                # 复用中的 run：采集端已经连着 WebSocket，无需再向 B 站要弹幕
                # 配置。只告诉采集端保留它即可——这避免了对 getDanmuInfo 的每轮
                # 高频调用（之前稳定的 run 也会被反复重拉配置，是风控的主因）。
                if run.get("reused_existing"):
                    keep_run_ids.append(run_id)
                    continue

                # 只有新建 run 才拉配置，并做有限重试；一次瞬时失败不再立刻
                # 把刚建好的 run 杀掉、下一轮又重建（即“监听不停断开”的抖动）。
                config = await self._fetch_collect_config_with_retry(collect_session, real_room_id)
                if config is None:
                    await self._db.finish_monitor_run(
                        run_id, error_message=f"collect config failed for room={real_room_id}"
                    )
                    logger.warning(
                        "failed to create collect config room=%s client=%s after %s attempts",
                        real_room_id,
                        client_id,
                        _COLLECT_CONFIG_ATTEMPTS,
                    )
                    continue
                tasks.append(
                    {
                        "run_id": run_id,
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

        logger.info(
            "claimed collector tasks client=%s tasks=%s keep=%s effective_limit=%s owned_rooms=%s",
            client_id,
            [int(task["run_id"]) for task in tasks],
            keep_run_ids,
            effective_limit,
            sorted(owned_room_ids),
        )
        return ClaimedTaskResult(tasks=tasks, keep_run_ids=keep_run_ids)
