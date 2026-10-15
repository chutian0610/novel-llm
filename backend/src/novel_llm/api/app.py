"""FastAPI application factory for the Novel LLM API."""

from fastapi import FastAPI

from novel_llm.api import health, v1
from novel_llm.config import Config, load_config


def create_app(config: Config | None = None) -> FastAPI:
    app = FastAPI(title="Novel LLM API", version="0.1.0")
    app.state.config = config if config is not None else load_config()
    app.include_router(health.router)
    app.include_router(v1.router, prefix="/api/v1")
    return app
