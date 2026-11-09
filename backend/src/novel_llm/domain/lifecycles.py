"""The Job and Workflow Run lifecycles (ADR-0003).

``python-statemachine`` expresses and validates the outer transitions in
memory; database rows stay authoritative — every persisted change is a
conditional UPDATE on the expected current state, and these machines are the
only authority on which transitions may happen between which states.

Job states are the closed set from the spec. Workflow Run states are the
business-facing subset. Terminal states are immutable: recovery creates a new
attempt or run rather than reopening history, which is why no event leaves a
``final`` state.
"""

from statemachine import State, StateMachine
from statemachine.exceptions import InvalidStateValue
from statemachine.exceptions import TransitionNotAllowed as MachineTransitionNotAllowed

JOB_STATES: frozenset[str] = frozenset(
    {
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
)

RUN_STATES: frozenset[str] = frozenset({"pending", "running", "succeeded", "failed", "cancelled"})

# ADR-0003: failures classify into this closed set; only transient failures
# retry automatically.
FAILURE_CLASSES: frozenset[str] = frozenset(
    {
        "transient",
        "permanent",
        "cancelled",
        "timeout",
        "quota_exhausted",
        "upstream_invalid",
    }
)


class JobLifecycle(StateMachine):
    """The outer Job lifecycle. Control requests (pause, cancel) are
    cooperative: they land as ``*_requested`` states and are honored at safe
    checkpoints by the Worker."""

    ready = State(initial=True)
    leased = State()
    running = State()
    pause_requested = State()
    paused = State()
    cancel_requested = State()
    cancelling = State()
    interrupted = State()
    succeeded = State(final=True)
    failed = State(final=True)
    cancelled = State(final=True)
    skipped = State(final=True)

    # Queue: a ready Job is claimed (leased) and begins executing.
    claim = ready.to(leased)
    begin = leased.to(running)
    # The claim is given back before execution starts (worker shutdown).
    release = leased.to(ready)
    # A dead Worker's lease expires; recovery (take_over) starts a new attempt.
    # A pause request pending on a dead Worker is consumed by the
    # interruption: recovery resumes execution rather than freezing it.
    interrupt = (
        leased.to(interrupted)
        | running.to(interrupted)
        | pause_requested.to(interrupted)
        | cancel_requested.to(interrupted)
        | cancelling.to(interrupted)
    )
    take_over = interrupted.to(leased)
    # Transient failures retry automatically, under database-owned settings.
    schedule_retry = running.to(ready)
    # Cooperative pause: requested mid-flight, honored at a safe checkpoint.
    request_pause = running.to(pause_requested)
    pause = pause_requested.to(paused)
    abandon_pause = pause_requested.to(running)
    resume = paused.to(ready)
    # Cooperative cancel: requested mid-flight, honored at a safe checkpoint.
    # A Job with no in-flight work (leased, paused) is cancelled directly.
    request_cancel = running.to(cancel_requested) | pause_requested.to(cancel_requested)
    cancel = leased.to(cancelled) | paused.to(cancelled) | cancel_requested.to(cancelling)
    finish_cancel = cancelling.to(cancelled)
    # Outcomes. After cancel_requested the machine refuses `complete`: a
    # cancellation request preempts the normal outcome (ADR-0003).
    complete = running.to(succeeded)
    fail = running.to(failed)
    skip = ready.to(skipped)


class RunLifecycle(StateMachine):
    """The business-facing Workflow Run lifecycle (the subset of the Job
    states a run reports)."""

    pending = State(initial=True)
    running = State()
    succeeded = State(final=True)
    failed = State(final=True)
    cancelled = State(final=True)

    begin = pending.to(running)
    complete = running.to(succeeded)
    fail = running.to(failed)
    cancel = pending.to(cancelled) | running.to(cancelled)


def transition_table(machine: type[StateMachine]) -> dict[tuple[str, str], str]:
    """The (state, event) -> target table, derived from the machine itself so
    tests and callers never restate the transitions by hand."""
    table: dict[tuple[str, str], str] = {}
    for state in machine.states:
        for transition in state.transitions:
            target = transition.target
            # Both lifecycles are flat state sets; the library's compound
            # (parallel) targets carry a set instead of a single State.
            if not isinstance(target, State):
                raise TypeError(f"unsupported compound transition target from {state.id}")
            for event in transition.events:
                table[(state.id, event.id)] = target.id
    return table


def transition_target(machine: type[StateMachine], current: str, event: str) -> str:
    """Validate one (current, event) transition against the machine and return
    the target state. The machine refuses anything not in its table — including
    terminal-state exits — by raising ``TransitionNotAllowed``; an unknown
    state is a caller bug and raises ``ValueError``."""
    try:
        instance = machine(start_value=current)
    except InvalidStateValue:
        raise ValueError(f"{current!r} is not a state of {machine.__name__}") from None
    try:
        getattr(instance, event)()
    except MachineTransitionNotAllowed:
        raise
    configuration = instance.configuration
    # Both lifecycles are flat state sets: exactly one state after a
    # transition. (python-statemachine 3.x models the current state as the
    # `configuration` set; `current_state` is the deprecated spelling.)
    if len(configuration) != 1:
        raise TypeError(f"unexpected compound configuration after {event!r} from {current!r}")
    return next(iter(configuration)).id
