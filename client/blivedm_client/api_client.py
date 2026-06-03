from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import aiohttp

from .config import Settings


@dataclass(frozen=True)
class CollectTask:
    """API 下发给采集端的一次 WebSocket 采集任务。"""

    run_id: int
    room_id: int
    configured_room_id: int
    room_title: str
    anchor_uid: Optional[int]
    uid: int
    buvid: str
    token: Optional[str]
    host_list: List[Dict[str, Any]]
    heartbeat_interval: int
    issued_at: str

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "CollectTask":
        return cls(
            run_id=int(payload["run_id"]),
            room_id=int(payload["room_id"]),
            configured_room_id=int(payload.get("configured_room_id") or payload["room_id"]),
            room_title=str(payload.get("room_title") or ""),
            anchor_uid=int(payload["anchor_uid"]) if payload.get("anchor_uid") is not None else None,
            uid=int(payload.get("uid") or 0),
            buvid=str(payload.get("buvid") or ""),
            token=payload.get("token"),
            host_list=list(payload.get("host_list") or []),
            heartbeat_interval=int(payload.get("heartbeat_interval") or 30),
            issued_at=str(payload.get("issued_at") or ""),
        )

    def config_fingerprint(self) -> tuple:
        # 用关键连接参数生成指纹，后续如果需要热更新任务配置，
        # 可以据此判断当前 WebSocket 是否需要重建。
        hosts = tuple((item.get("host"), item.get("wss_port")) for item in self.host_list)
        return (self.room_id, self.uid, self.buvid, self.token, hosts, self.heartbeat_interval)


class ApiClient:
    def __init__(self, settings: Settings, session: aiohttp.ClientSession):
        self._settings = settings
        self._session = session

    async def fetch_tasks(self) -> List[CollectTask]:
        # client_id 用来区分不同采集进程，API 会用它维护 run 租约和心跳。
        async with self._session.get(
            self._settings.task_url,
            params={"client_id": self._settings.client_id},
        ) as response:
            response.raise_for_status()
            payload = await response.json()
        return [CollectTask.from_payload(item) for item in payload.get("items", [])]

    async def heartbeat(self, run_id: int) -> bool:
        async with self._session.post(
            self._settings.heartbeat_url(run_id),
            json={"client_id": self._settings.client_id},
        ) as response:
            if response.status == 404:
                return False
            response.raise_for_status()
            return True

    async def stop_run(self, run_id: int, reason: str) -> None:
        async with self._session.post(
            self._settings.stop_url(run_id),
            json={"client_id": self._settings.client_id, "reason": reason},
        ) as response:
            response.raise_for_status()
