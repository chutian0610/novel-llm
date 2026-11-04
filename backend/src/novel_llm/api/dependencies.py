"""FastAPI dependency accessors."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from novel_llm.config import Config


def get_config(request: Request) -> Config:
    return request.app.state.config


def get_db_engine(request: Request) -> AsyncEngine:
    return request.app.state.db_engine


async def get_db_session(
    engine: Annotated[AsyncEngine, Depends(get_db_engine)],
) -> AsyncIterator[AsyncSession]:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session
