"""FastAPI dependency accessors."""

from fastapi import Request

from novel_llm.config import Config


def get_config(request: Request) -> Config:
    return request.app.state.config
