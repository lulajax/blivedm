import asyncio

from blivedm_client.api_client import TaskSnapshot
from blivedm_client.config import Settings
from blivedm_client.monitor import CollectClientService


class FakeApi:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    async def fetch_tasks(self):
        return self.snapshot


def make_settings():
    return Settings(
        api_base_url="http://127.0.0.1:8000",
        client_id="client-a",
        task_poll_interval_seconds=10,
        run_heartbeat_interval_seconds=10,
        batch_size=50,
        flush_interval_seconds=1.0,
    )


def test_sync_tasks_keeps_active_run_when_api_marks_it_keepalive():
    async def run_test():
        service = CollectClientService(make_settings())
        service._api = FakeApi(TaskSnapshot(tasks=[], keep_run_ids={1}))
        service._active = {1: object(), 2: object()}
        stopped = []

        async def fake_stop_run(run_id, reason):
            stopped.append((run_id, reason))
            service._active.pop(run_id, None)

        service.stop_run = fake_stop_run

        await service._sync_tasks()

        assert stopped == [(2, "collector task revoked")]
        assert set(service._active.keys()) == {1}

    asyncio.run(run_test())
