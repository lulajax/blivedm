from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import aiohttp
import blivedm

from .config import Settings

logger = logging.getLogger(__name__)


class CollectorPublisher:
    """把解码后的原始命令批量转发给 API 调度端。"""

    def __init__(self, settings: Settings, session: aiohttp.ClientSession):
        self._settings = settings
        self._session = session
        self._queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
        self._task: Optional[asyncio.Task] = None
        self._stopping = False

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stopping = False
        self._task = asyncio.create_task(self._loop(), name="collector-publisher")

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def publish(self, run_id: int, room_id: int, command: Dict[str, Any]) -> None:
        self._queue.put_nowait(
            {
                "run_id": run_id,
                "room_id": room_id,
                "cmd": command.get("cmd", ""),
                "raw": command,
                "received_at": datetime.now().isoformat(timespec="seconds"),
            }
        )

    async def _loop(self) -> None:
        while not self._stopping:
            batch = await self._next_batch()
            if not batch:
                continue
            await self._post_batch(batch)
            for _item in batch:
                self._queue.task_done()

    async def _next_batch(self) -> List[Dict[str, Any]]:
        first = await self._queue.get()
        batch = [first]
        deadline = asyncio.get_running_loop().time() + self._settings.flush_interval_seconds
        while len(batch) < self._settings.batch_size:
            timeout = deadline - asyncio.get_running_loop().time()
            if timeout <= 0:
                break
            try:
                batch.append(await asyncio.wait_for(self._queue.get(), timeout=timeout))
            except asyncio.TimeoutError:
                break
        return batch

    async def _post_batch(self, batch: List[Dict[str, Any]]) -> None:
        payload = {
            "source": "collector-client",
            "client_id": self._settings.client_id,
            "received_at": datetime.now().isoformat(timespec="seconds"),
            "events": batch,
        }
        while True:
            # 批量上报失败时不丢弃事件，短暂等待后继续重试。
            try:
                async with self._session.post(self._settings.event_batch_url, json=payload) as response:
                    if response.status < 400:
                        return
                    body = await response.text()
                    logger.warning("collector rejected batch status=%s body=%s", response.status, body[:500])
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("failed to post collector batch size=%s: %s", len(batch), exc)
            await asyncio.sleep(2)


class ForwardingHandler(blivedm.BaseHandler):
    """最小化 blivedm handler：只转发原始命令，解析留给 API。"""

    def __init__(self, publisher: CollectorPublisher, run_id: int, room_id: int):
        self._publisher = publisher
        self._run_id = run_id
        self._room_id = room_id

    def handle(self, client: blivedm.BLiveClient, command: Dict[str, Any]):
        room_id = int(client.room_id or self._room_id)
        self._publisher.publish(self._run_id, room_id, command)
