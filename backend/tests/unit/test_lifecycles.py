"""Seam 2 — the Job and Workflow Run lifecycles (ADR-0003).

`python-statemachine` expresses and validates the outer transitions in memory
while database rows stay authoritative. These tests pin the closed state sets
and the full transition tables, so an illegal outer transition is a loud test
failure instead of a silent state corruption.
"""

import pytest
from statemachine.exceptions import TransitionNotAllowed as MachineTransitionNotAllowed

from novel_llm.domain.lifecycles import (
    FAILURE_CLASSES,
    JOB_STATES,
    RUN_STATES,
    JobLifecycle,
    RunLifecycle,
    transition_table,
    transition_target,
)

# The closed Job state table from ADR-0003, read as the spec:
# (current state, event) -> next state.
JOB_TRANSITIONS: dict[tuple[str, str], str] = {
    ("ready", "claim"): "leased",
    ("ready", "skip"): "skipped",
    ("leased", "begin"): "running",
    ("leased", "release"): "ready",
    ("leased", "cancel"): "cancelled",
    ("leased", "interrupt"): "interrupted",
    ("running", "complete"): "succeeded",
    ("running", "fail"): "failed",
    ("running", "schedule_retry"): "ready",
    ("running", "request_pause"): "pause_requested",
    ("running", "request_cancel"): "cancel_requested",
    ("running", "interrupt"): "interrupted",
    ("pause_requested", "pause"): "paused",
    ("pause_requested", "abandon_pause"): "running",
    ("pause_requested", "request_cancel"): "cancel_requested",
    ("paused", "resume"): "ready",
    ("paused", "cancel"): "cancelled",
    ("cancel_requested", "cancel"): "cancelling",
    ("cancel_requested", "interrupt"): "interrupted",
    ("cancelling", "finish_cancel"): "cancelled",
    ("cancelling", "interrupt"): "interrupted",
    ("interrupted", "take_over"): "leased",
}

# Workflow Run states are the business-facing subset (ADR-0003).
RUN_TRANSITIONS: dict[tuple[str, str], str] = {
    ("pending", "begin"): "running",
    ("pending", "cancel"): "cancelled",
    ("running", "complete"): "succeeded",
    ("running", "fail"): "failed",
    ("running", "cancel"): "cancelled",
}

JOB_TERMINAL_STATES = {"succeeded", "failed", "cancelled", "skipped"}
RUN_TERMINAL_STATES = {"succeeded", "failed", "cancelled"}


def test_job_states_are_exactly_the_closed_set_from_the_spec() -> None:
    assert set(JOB_STATES) == {
        "ready",
        "leased",
        "running",
        "pause_requested",
        "paused",
        "cancel_requested",
        "cancelling",
        "interrupted",
        "succeeded",
        "failed",
        "cancelled",
        "skipped",
    }


def test_workflow_run_states_are_the_business_facing_subset() -> None:
    assert set(RUN_STATES) == {"pending", "running", "succeeded", "failed", "cancelled"}


def test_job_transitions_match_the_declared_table() -> None:
    assert transition_table(JobLifecycle) == JOB_TRANSITIONS


def test_workflow_run_transitions_match_the_declared_table() -> None:
    assert transition_table(RunLifecycle) == RUN_TRANSITIONS


@pytest.mark.parametrize("state", sorted(JOB_TERMINAL_STATES))
def test_job_terminal_states_have_no_outgoing_transition(state: str) -> None:
    outgoing = [
        (current, event) for current, event in transition_table(JobLifecycle) if current == state
    ]
    assert outgoing == [], f"{state} is terminal and must never leave"


@pytest.mark.parametrize("state", sorted(RUN_TERMINAL_STATES))
def test_workflow_run_terminal_states_have_no_outgoing_transition(state: str) -> None:
    outgoing = [
        (current, event) for current, event in transition_table(RunLifecycle) if current == state
    ]
    assert outgoing == [], f"{state} is terminal and must never leave"


def test_job_terminal_states_are_exactly_the_states_without_outgoing_transitions() -> None:
    with_outgoing = {current for current, _ in transition_table(JobLifecycle)}
    assert set(JOB_STATES) - with_outgoing == JOB_TERMINAL_STATES


@pytest.mark.parametrize(
    ("current", "event", "expected"),
    [(current, event, target) for (current, event), target in sorted(JOB_TRANSITIONS.items())],
)
def test_every_declared_job_transition_validates_to_its_target(
    current: str, event: str, expected: str
) -> None:
    assert transition_target(JobLifecycle, current, event) == expected


@pytest.mark.parametrize(
    ("current", "event"),
    [
        ("running", "claim"),  # only a ready Job is claimed
        ("ready", "complete"),  # nothing completes before it runs
        ("succeeded", "claim"),  # terminal states are immutable
        ("failed", "take_over"),
        ("paused", "claim"),
        ("cancel_requested", "complete"),  # cancellation preempts the normal outcome
    ],
)
def test_an_illegal_job_transition_is_refused(current: str, event: str) -> None:
    with pytest.raises(MachineTransitionNotAllowed):
        transition_target(JobLifecycle, current, event)


def test_an_unknown_job_state_is_refused() -> None:
    with pytest.raises(ValueError):
        transition_target(JobLifecycle, "deployed", "claim")


def test_failure_classes_are_exactly_the_closed_set_from_the_spec() -> None:
    assert set(FAILURE_CLASSES) == {
        "transient",
        "permanent",
        "cancelled",
        "timeout",
        "quota_exhausted",
        "upstream_invalid",
    }
