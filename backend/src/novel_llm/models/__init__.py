"""Model execution (ADR-0006/0008): semantically versioned prompt templates,
the deterministic fake provider, the typed output contracts, and the gateway
that calls a model through a Model Profile — validating the output per its
contract, repairing structurally invalid output at most once, and persisting
full traces for every call (original and repair alike).
"""
