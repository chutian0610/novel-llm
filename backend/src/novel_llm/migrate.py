"""CLI: apply migrations to the configured database (used at container start)."""

import asyncio

from novel_llm.config import load_config
from novel_llm.infrastructure.migrations import upgrade_to_head


def main() -> None:
    asyncio.run(upgrade_to_head(load_config().database.url))


if __name__ == "__main__":
    main()
