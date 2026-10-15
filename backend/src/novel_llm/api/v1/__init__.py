"""Versioned public API surface (/api/v1, ADR-0008).

Domain resources land here in later Phase 1 tickets; the skeleton exposes the
versioned root so the generated typed client and drift check have a stable
anchor.
"""

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class APIRoot(BaseModel):
    name: str
    version: str


@router.get("", response_model=APIRoot)
async def api_root() -> APIRoot:
    return APIRoot(name="novel-llm", version="v1")
