"""Alembic wiring — the only schema-change mechanism (ADR-0002/0003).

The script directory lives next to the package in the source tree and beside
the package in the container; ``NOVEL_LLM_ALEMBIC_DIR`` overrides both.
"""

import asyncio
import os
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory

_ALEMBIC_DIR_VAR = "NOVEL_LLM_ALEMBIC_DIR"


def alembic_dir() -> Path:
    override = os.environ.get(_ALEMBIC_DIR_VAR)
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "alembic"


def _alembic_config(database_url: str) -> AlembicConfig:
    directory = alembic_dir()
    cfg = AlembicConfig(str(directory / "alembic.ini"))
    cfg.set_main_option("script_location", str(directory))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


async def upgrade_to_head(database_url: str) -> None:
    """Apply all pending migrations to the database at ``database_url``.

    Alembic's async template drives its own event loop, so the command runs in
    a worker thread; that way it also works inside an already-running loop
    (tests, API-driven migrations).
    """
    await asyncio.to_thread(command.upgrade, _alembic_config(database_url), "head")


def head_revision() -> str:
    """The head revision of the code-owned migration scripts."""
    head = ScriptDirectory(str(alembic_dir())).get_current_head()
    if head is None:
        raise RuntimeError(f"no migration scripts found under {alembic_dir()}")
    return head
