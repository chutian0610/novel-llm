"""Domain rule violations, mapped to problem-details with stable error codes
at the API boundary (ADR-0008). Domain code never imports HTTP machinery."""

from collections.abc import Mapping


class DomainError(Exception):
    """A domain rule violation with an HTTP presentation."""

    status: int = 500
    code: str = "internal_error"
    title: str = "Internal error"

    def __init__(self, detail: str = "", headers: Mapping[str, str] | None = None) -> None:
        super().__init__(detail or self.title)
        self.detail = detail
        self.headers = dict(headers) if headers else None


class ProjectNotFound(DomainError):
    status = 404
    code = "project_not_found"
    title = "Project not found"


class IdempotencyKeyConflict(DomainError):
    status = 409
    code = "idempotency_key_conflict"
    title = "Idempotency key already used with a different request"


class ConcurrencyConflict(DomainError):
    status = 412
    code = "etag_mismatch"
    title = "ETag does not match the current version"


class ProjectArchived(DomainError):
    status = 409
    code = "project_archived"
    title = "Project is archived"


class InvalidCursor(DomainError):
    status = 400
    code = "invalid_cursor"
    title = "Malformed pagination cursor"


class WorkflowRunNotFound(DomainError):
    status = 404
    code = "workflow_run_not_found"
    title = "Workflow Run not found"


class JobNotFound(DomainError):
    status = 404
    code = "job_not_found"
    title = "Job not found"


class JobStateConflict(DomainError):
    """A Job control action (pause, resume, cancel) the Job's current state
    does not allow — pause and resume are cooperative control requests with a
    place in the lifecycle, not generic state patches (ADR-0003, ADR-0008)."""

    status = 409
    code = "job_state_conflict"
    title = "Job state does not allow this action"


class UnknownWorkflowDefinition(DomainError):
    status = 400
    code = "unknown_workflow_definition"
    title = "Unknown Workflow Definition"


class InvalidWorkflowPayload(DomainError):
    status = 400
    code = "invalid_workflow_payload"
    title = "Workflow Run payload does not match the definition schema"
