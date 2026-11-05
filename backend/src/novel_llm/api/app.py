"""FastAPI application factory for the Novel LLM API."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

import novel_llm.workflows.trivial  # noqa: F401 — registers the workflow definitions
from novel_llm.api import health, problems, v1
from novel_llm.config import Config, load_config
from novel_llm.infrastructure.db import create_db_engine


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """The database engine lives with the app, as it does under uvicorn."""
    engine = create_db_engine(app.state.config.database.url)
    app.state.db_engine = engine
    try:
        yield
    finally:
        await engine.dispose()


def create_app(config: Config | None = None) -> FastAPI:
    app = FastAPI(title="Novel LLM API", version="0.1.0", lifespan=_lifespan)
    app.state.config = config if config is not None else load_config()
    problems.install_problem_handlers(app)
    app.include_router(health.router)
    app.include_router(v1.router, prefix="/api/v1")
    return app
