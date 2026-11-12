"""The deterministic fake model provider (ADR-0003/0008): no network, no
randomness — the same prompt always yields the same completion and the same
token counts, and the corruption lever lets the framework tests drive the
repair contract end to end. It implements both contract shapes: Markdown
plus metadata for writing, and typed JSON for the evaluation kinds."""

import hashlib
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    prompt: str
    model: str
    output_kind: str
    max_tokens: int = 1024


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    completion: str
    input_tokens: int
    output_tokens: int
    metadata: dict = field(default_factory=dict)


def _seed(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _typed_json(output_kind: str, seed: str) -> str:
    score = 60 + int(seed[:2], 16) % 41
    verdict = "approve" if seed[2] in "0123456789abcdef"[:8] else "revise"
    if output_kind == "evaluate":
        return (
            f'{{"score": {score}, "verdict": "{verdict}", "notes": "fake 评估（seed={seed[:8]}）"}}'
        )
    if output_kind == "diagnose":
        issue = f"fake 问题（seed={seed[:8]}）"
        return f'{{"issues": [{{"description": "{issue}", "severity": "minor"}}]}}'
    if output_kind == "extract":
        return f'{{"entities": [{{"name": "灯（seed={seed[:4]}）", "kind": "item"}}]}}'
    if output_kind == "project":
        return f'{{"state_summary": "截至本章（seed={seed[:8]}）：灯已交接。"}}'
    raise ValueError(f"{output_kind!r} is not a typed output kind")


class FakeProvider:
    """Deterministic and offline: completions derive from the prompt's hash,
    token counts from character lengths, and `corrupt_output` breaks the JSON
    shape of typed outputs exactly once — the repair contract's lever."""

    def complete(
        self, request: ProviderRequest, *, corrupt_output: bool = False
    ) -> ProviderResponse:
        seed = _seed(request.prompt)
        if request.output_kind == "write":
            completion = (
                f"# 试写（fake）\n\n灯影落在阶前，少年把灯举过头顶。"
                f"（seed={seed[:12]}，model={request.model}）"
            )
        else:
            if corrupt_output:
                completion = '{"score": 不是合法 JSON'
            else:
                completion = _typed_json(request.output_kind, seed)
        return ProviderResponse(
            completion=completion,
            input_tokens=len(request.prompt),
            output_tokens=len(completion),
            metadata={"provider": "mock", "seed": seed[:12], "finish_reason": "stop"},
        )
