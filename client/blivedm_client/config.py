from __future__ import annotations

import os
import socket
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    api_base_url: str
    client_id: str
    task_poll_interval_seconds: int
    run_heartbeat_interval_seconds: int
    batch_size: int
    flush_interval_seconds: float

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        default_client_id = f"{socket.gethostname()}:{os.getpid()}"
        return cls(
            api_base_url=os.getenv("API_BASE_URL", "http://127.0.0.1:8000").rstrip("/"),
            client_id=os.getenv("COLLECTOR_CLIENT_ID", default_client_id),
            task_poll_interval_seconds=max(2, int(os.getenv("TASK_POLL_INTERVAL_SECONDS", "10"))),
            run_heartbeat_interval_seconds=max(5, int(os.getenv("RUN_HEARTBEAT_INTERVAL_SECONDS", "10"))),
            batch_size=max(1, int(os.getenv("COLLECTOR_BATCH_SIZE", "50"))),
            flush_interval_seconds=max(0.2, float(os.getenv("COLLECTOR_FLUSH_INTERVAL_SECONDS", "1.0"))),
        )

    @property
    def task_url(self) -> str:
        return f"{self.api_base_url}/internal/collector/tasks"

    @property
    def event_batch_url(self) -> str:
        return f"{self.api_base_url}/internal/events/batch"

    def heartbeat_url(self, run_id: int) -> str:
        return f"{self.api_base_url}/internal/collector/runs/{run_id}/heartbeat"

    def stop_url(self, run_id: int) -> str:
        return f"{self.api_base_url}/internal/collector/runs/{run_id}/stop"

