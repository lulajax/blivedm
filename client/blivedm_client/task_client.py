from __future__ import annotations

from typing import Optional

import aiohttp
import blivedm

from .api_client import CollectTask


class TaskBLiveClient(blivedm.BLiveClient):
    """使用 API 下发 WebSocket 配置的 BLiveClient。

    原始 blivedm client 通常会在 init_room() 中调用 B 站 HTTP 接口。
    这里 API 调度端已经完成这一步，collector 只使用任务 payload
    启动 WebSocket 连接。
    """

    def __init__(self, task: CollectTask, *, session: Optional[aiohttp.ClientSession] = None):
        super().__init__(
            task.room_id,
            uid=task.uid,
            session=session,
            heartbeat_interval=task.heartbeat_interval,
        )
        self._task_config = task

    async def init_room(self) -> bool:
        self._room_id = self._task_config.room_id
        self._room_owner_uid = self._task_config.anchor_uid or 0
        self._uid = self._task_config.uid
        self._host_server_list = self._task_config.host_list
        self._host_server_token = self._task_config.token
        return bool(self._host_server_list)

    def _get_buvid(self) -> str:
        return self._task_config.buvid
