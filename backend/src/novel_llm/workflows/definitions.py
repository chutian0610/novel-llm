"""Workflow Definitions, versioned in code (ADR-0003).

A definition carries a stable key, a definition version, and a payload schema
version; a Workflow Run row binds the version that created it, so code
upgrades affect only new runs. The definition also owns the mapping from Job
transitions to Workflow Run transitions — the run state machine still
validates every mapped transition.

Registration is explicit: entrypoints (API factory, worker main) import the
modules that define workflows, keeping the import graph acyclic.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel


@dataclass(frozen=True, slots=True)
class WorkflowDefinition:
    key: str
    version: int
    payload_schema_version: int
    payload_model: type[BaseModel]
    job_type: str
    job_payload: Callable[[BaseModel], dict[str, Any]]
    # Job transition event -> Workflow Run transition event.
    run_transition_for_job_event: dict[str, str]


DEFINITIONS: dict[str, WorkflowDefinition] = {}


def register(definition: WorkflowDefinition) -> None:
    DEFINITIONS[definition.key] = definition


def resolve_definition(key: str) -> WorkflowDefinition | None:
    return DEFINITIONS.get(key)
