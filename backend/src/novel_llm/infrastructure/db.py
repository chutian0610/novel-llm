"""Async engine construction from configuration."""

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

_CONNECT_TIMEOUT_SECONDS = 3


def create_db_engine(database_url: str) -> AsyncEngine:
    """A short-timeout engine suited to health checks and small request work."""
    return create_async_engine(
        database_url,
        connect_args={
            "timeout": _CONNECT_TIMEOUT_SECONDS,
            "command_timeout": _CONNECT_TIMEOUT_SECONDS,
        },
    )
