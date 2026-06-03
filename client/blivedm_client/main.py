from __future__ import annotations

import asyncio
import logging
import signal

from .config import Settings
from .monitor import CollectClientService


async def run_client() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings.from_env()
    service = CollectClientService(settings)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    await service.start()
    try:
        await stop_event.wait()
    finally:
        await service.stop()


def main() -> None:
    asyncio.run(run_client())


if __name__ == "__main__":
    main()

