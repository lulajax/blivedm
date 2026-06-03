from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    mysql_host: str
    mysql_port: int
    mysql_database: str
    mysql_user: str
    mysql_password: str
    bili_sessdata: str
    room_poll_interval_seconds: int
    collector_stale_seconds: int
    collector_task_poll_interval_seconds: int
    bilibili_heartbeat_interval_seconds: int
    app_host: str
    app_port: int

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        return cls(
            mysql_host=os.getenv("MYSQL_HOST", "192.168.84.30"),
            mysql_port=int(os.getenv("MYSQL_PORT", "3306")),
            mysql_database=os.getenv("MYSQL_DATABASE", "blivedm"),
            mysql_user=os.getenv("MYSQL_USER", "blivedm"),
            mysql_password=os.getenv("MYSQL_PASSWORD", ""),
            bili_sessdata=os.getenv("BILI_SESSDATA", ""),
            room_poll_interval_seconds=max(5, int(os.getenv("ROOM_POLL_INTERVAL_SECONDS", "30"))),
            collector_stale_seconds=max(30, int(os.getenv("COLLECTOR_STALE_SECONDS", "90"))),
            collector_task_poll_interval_seconds=max(
                2,
                int(os.getenv("COLLECTOR_TASK_POLL_INTERVAL_SECONDS", "10")),
            ),
            bilibili_heartbeat_interval_seconds=max(
                5,
                int(os.getenv("BILIBILI_HEARTBEAT_INTERVAL_SECONDS", "30")),
            ),
            app_host=os.getenv("APP_HOST", "0.0.0.0"),
            app_port=int(os.getenv("APP_PORT", "8000")),
        )

    def validate(self) -> None:
        if not self.mysql_password:
            raise RuntimeError("MYSQL_PASSWORD is required")
