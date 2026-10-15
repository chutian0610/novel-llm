"""Worker entrypoint — idle skeleton.

The queue/claim loop arrives with the Job runtime ticket; this entrypoint
establishes the process, its configuration, and cooperative SIGTERM behavior.
"""

import asyncio
import logging
import signal

from novel_llm.config import load_config

log = logging.getLogger("novel_llm.worker")


async def main() -> None:
    config = load_config()
    logging.basicConfig(
        level=config.log.level,
        format="%(levelname)s %(name)s %(message)s",
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    log.info("worker started (no job types registered yet; the Job runtime arrives next)")
    log.info("storage at %s", config.storage.path)

    await stop.wait()
    log.info("worker stopped at cooperative checkpoint")


if __name__ == "__main__":
    asyncio.run(main())
