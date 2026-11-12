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


class VolumeNotFound(DomainError):
    status = 404
    code = "volume_not_found"
    title = "Volume not found"


class ChapterNotFound(DomainError):
    status = 404
    code = "chapter_not_found"
    title = "Chapter not found"


class OutlineNotFound(DomainError):
    status = 404
    code = "outline_not_found"
    title = "Outline version not found"


class BibleIdentityNotFound(DomainError):
    status = 404
    code = "identity_not_found"
    title = "Bible identity not found"


class IdentityNameTaken(DomainError):
    status = 409
    code = "identity_name_taken"
    title = "Bible identity name already taken in this Project"


class BibleEntryNotFound(DomainError):
    status = 404
    code = "bible_entry_not_found"
    title = "Bible entry not found"


class BibleNotFound(DomainError):
    status = 404
    code = "bible_not_found"
    title = "Published Bible version not found"


class BibleEntryPayloadInvalid(DomainError):
    status = 400
    code = "bible_entry_payload_invalid"
    title = "Bible entry detail does not match its type contract"


class CredentialNotFound(DomainError):
    status = 404
    code = "credential_not_found"
    title = "Provider credential not found"


class CredentialNameTaken(DomainError):
    status = 409
    code = "credential_name_taken"
    title = "A provider credential with this name already exists"


class ModelProfileNotFound(DomainError):
    status = 404
    code = "model_profile_not_found"
    title = "Model profile not found"


class ModelProfileNameTaken(DomainError):
    status = 409
    code = "model_profile_name_taken"
    title = "A model profile with this name already exists"


class ModelTraceNotFound(DomainError):
    status = 404
    code = "model_trace_not_found"
    title = "Model trace not found"


class StructureOrderInvalid(DomainError):
    """A reorder that does not name every sibling exactly once, in the new
    order — refused before any row moves."""

    status = 400
    code = "structure_order_invalid"
    title = "The reorder does not match the current structure"


class InvalidOutlinePayload(DomainError):
    status = 400
    code = "invalid_outline_payload"
    title = "Outline payload does not match the schema"


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
