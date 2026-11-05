"""Job handler registry: job_type -> handler (ADR-0003).

Handlers are code-owned like Workflow Definitions; the Worker resolves the
handler at execution time, while timeout, heartbeat, retry, and backoff come
from the database-owned job_types row.
"""

from collections.abc import Awaitable, Callable

from novel_llm.domain.job_execution import JobContext

Handler = Callable[[JobContext], Awaitable[None]]

HANDLERS: dict[str, Handler] = {}


def register_handler(job_type: str, handler: Handler) -> None:
    HANDLERS[job_type] = handler


def resolve_handler(job_type: str) -> Handler | None:
    return HANDLERS.get(job_type)
