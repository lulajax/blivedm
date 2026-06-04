from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Dict, Optional, Set

import aiohttp

from .api_client import ApiClient, CollectTask
from .collector import CollectorPublisher, ForwardingHandler
from .config import Settings
from .task_client import TaskBLiveClient

logger = logging.getLogger(__name__)


@dataclass
class ActiveTask:
    task: CollectTask
    client: TaskBLiveClient
    network_task: asyncio.Task
    heartbeat_task: asyncio.Task
    stop_reason: str = "collector stopped"


class CollectClientService:
    """长期运行的采集服务，负责同步本地 WebSocket run。"""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._session: Optional[aiohttp.ClientSession] = None
        self._api: Optional[ApiClient] = None
        self._publisher: Optional[CollectorPublisher] = None
        self._task: Optional[asyncio.Task] = None
        self._stopping = False
        self._active: Dict[int, ActiveTask] = {}

    @property
    def active_run_ids(self) -> Set[int]:
        return set(self._active.keys())

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.is_running:
            return
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        self._api = ApiClient(self._settings, self._session)
        self._publisher = CollectorPublisher(self._settings, self._session)
        await self._publisher.start()
        self._stopping = False
        self._task = asyncio.create_task(self._loop(), name="collector-client-loop")

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        for run_id in list(self._active.keys()):
            await self.stop_run(run_id, "collector stopped")

        if self._publisher is not None:
            await self._publisher.stop()
            self._publisher = None

        if self._session is not None:
            await self._session.close()
            self._session = None

    async def _loop(self) -> None:
        logger.info("collector client loop started client_id=%s", self._settings.client_id)
        while not self._stopping:
            try:
                await self._sync_tasks()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("collector client sync failed")
            await asyncio.sleep(self._settings.task_poll_interval_seconds)
        logger.info("collector client loop stopped")

    async def _sync_tasks(self) -> None:
        if self._api is None:
            raise RuntimeError("api client is not initialized")

        # API 返回当前 client 应该持有的 run 集合。新增 run 在本地启动，
        # 被撤销的 run 会停止并回报给 API。
        snapshot = await self._api.fetch_tasks()
        tasks = snapshot.tasks
        desired = {task.run_id: task for task in tasks}
        logger.info(
            "collector task sync active=%s desired=%s keep=%s",
            sorted(self._active.keys()),
            sorted(desired.keys()),
            sorted(snapshot.keep_run_ids),
        )

        for task in tasks:
            active = self._active.get(task.run_id)
            if active is None:
                await self.start_task(task)

        for run_id in list(self._active.keys()):
            if run_id not in desired and run_id not in snapshot.keep_run_ids:
                await self.stop_run(run_id, "collector task revoked")

    async def start_task(self, task: CollectTask) -> None:
        if self._session is None or self._publisher is None:
            raise RuntimeError("collector service is not initialized")

        client = TaskBLiveClient(task, session=self._session)
        client.set_handler(ForwardingHandler(self._publisher, task.run_id, task.room_id))
        active = ActiveTask(
            task=task,
            client=client,
            network_task=asyncio.create_task(self._run_client(task, client), name=f"collector-ws-{task.run_id}"),
            heartbeat_task=asyncio.create_task(self._heartbeat_loop(task.run_id), name=f"collector-heartbeat-{task.run_id}"),
        )
        self._active[task.run_id] = active
        logger.info("started collector run=%s room=%s title=%s", task.run_id, task.room_id, task.room_title)

    async def stop_run(self, run_id: int, reason: str) -> None:
        active = self._active.get(run_id)
        if active is None:
            return
        active.stop_reason = reason
        if active.heartbeat_task is not asyncio.current_task():
            active.heartbeat_task.cancel()
            try:
                await active.heartbeat_task
            except asyncio.CancelledError:
                pass

        if active.client.is_running:
            active.client.stop()
        try:
            await asyncio.wait_for(active.network_task, timeout=10)
        except asyncio.CancelledError:
            pass
        except asyncio.TimeoutError:
            active.network_task.cancel()
            try:
                await active.network_task
            except asyncio.CancelledError:
                pass

        if self._api is not None:
            try:
                await self._api.stop_run(run_id, reason)
            except Exception:  # noqa: BLE001
                logger.exception("failed to report collector stop run=%s", run_id)

        self._active.pop(run_id, None)
        logger.info("stopped collector run=%s reason=%s", run_id, reason)

    async def _heartbeat_loop(self, run_id: int) -> None:
        if self._api is None:
            raise RuntimeError("api client is not initialized")
        while True:
            ok = await self._api.heartbeat(run_id)
            if not ok:
                logger.warning("collector heartbeat rejected run=%s", run_id)
                asyncio.create_task(self.stop_run(run_id, "collector run no longer active"))
                return
            await asyncio.sleep(self._settings.run_heartbeat_interval_seconds)

    async def _run_client(self, task: CollectTask, client: TaskBLiveClient) -> None:
        error_message: Optional[str] = None
        client.start()
        try:
            await client.join()
        except asyncio.CancelledError:
            error_message = "task cancelled"
            raise
        except Exception as exc:  # noqa: BLE001
            error_message = str(exc)
            logger.exception("collector websocket failed run=%s room=%s", task.run_id, task.room_id)
        finally:
            logger.info(
                "collector websocket finished run=%s room=%s error=%s",
                task.run_id,
                task.room_id,
                error_message,
            )
            # 无论正常停止还是异常退出，都要关闭 B 站连接并上报停止原因，
            # 避免 monitor_runs 中留下长时间 running 的脏数据。
            await client.stop_and_close()
            active = self._active.get(task.run_id)
            if active is not None and error_message is None:
                error_message = active.stop_reason
            if active is not None:
                active.stop_reason = error_message or "collector websocket stopped"
                if active.heartbeat_task is not asyncio.current_task():
                    active.heartbeat_task.cancel()
                    try:
                        await active.heartbeat_task
                    except asyncio.CancelledError:
                        pass
                if self._api is not None:
                    try:
                        await self._api.stop_run(task.run_id, active.stop_reason)
                    except Exception:  # noqa: BLE001
                        logger.exception("failed to report collector websocket stop run=%s", task.run_id)
                self._active.pop(task.run_id, None)
