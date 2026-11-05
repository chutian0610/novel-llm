"""Worker entrypoint: claims Jobs transactionally from PostgreSQL and
executes them through the runtime (ADR-0003). SIGINT/SIGTERM trigger the
graceful shutdown sequence — stop claiming, request cooperative
cancellation, wait the graceful timeout, record interrupted checkpoints if
forced to exit."""

import asyncio
import logging
import signal

import novel_llm.workflows.trivial  # noqa: F401 — registers the workflow definitions
from novel_llm.config import load_config
from novel_llm.infrastructure.db import create_db_engine
from novel_llm.worker.runtime import Worker

log = logging.getLogger("novel_llm.worker")


async def main() -> None:
    config = load_config()
    logging.basicConfig(
        level=config.log.level,
        format="%(levelname)s %(name)s %(message)s",
    )

    engine = create_db_engine(config.database.url)
    worker = Worker(engine, config.worker)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, worker.stop)

    log.info(
        "worker started (concurrency=%s, poll=%ss)",
        config.worker.concurrency,
        config.worker.poll_seconds,
    )
    try:
        await worker.run()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
