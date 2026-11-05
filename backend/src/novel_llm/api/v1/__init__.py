"""Versioned public API surface (/api/v1, ADR-0008).

Domain resources land here in their Phase 1 tickets; the skeleton exposes the
versioned root so the generated typed client and drift check have a stable
anchor.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from novel_llm.api.v1 import projects

router = APIRouter()
router.include_router(projects.router)


class APIRoot(BaseModel):
    name: str
    version: str


@router.get("", response_model=APIRoot)
async def api_root() -> APIRoot:
    return APIRoot(name="novel-llm", version="v1")
