# Coding standards

Judgement-call rules the reviewer agent enforces on diffs. A rule belongs here
only when no guardrail (`ruff`, `mypy`, `make check`) can ever substitute for
the judgement; mechanical violations belong in the linters instead. Keep this
file small: every rule here spends reviewer attention on every future diff.

## Testing

- **Timing assertions measure magnitude, not just ordering.** An assertion like
  `later.started_at >= earlier.finished_at` is vacuous for backoff, lease, and
  timeout behaviour: it holds even when the delay was never enforced. Assert the
  measured gap against the configured magnitude (e.g. `gap >= backoff_seconds`,
  from the `job_types` row the behaviour is owned by), so deleting the mechanism
  turns the test red.
