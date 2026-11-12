"""Prompt templates, semantically versioned in code (ADR-0008).

A template carries a stable key and a semantic version; the registry holds
every version ever shipped, so a historical run's trace can always be
explained by the exact template that rendered it. The gateway renders the
template fully and the rendered prompt lands in the trace verbatim."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    key: str
    version: int
    template: str

    def render(self, **variables: str | int) -> str:
        return self.template.format(**variables)


TEMPLATES: dict[tuple[str, int], PromptTemplate] = {}


def register(template: PromptTemplate) -> None:
    TEMPLATES[(template.key, template.version)] = template


def get_template(key: str, version: int) -> PromptTemplate | None:
    return TEMPLATES.get((key, version))


register(
    PromptTemplate(
        key="probe.write",
        version=1,
        template=(
            "你为《{work_title}》撰写第 {chapter_number} 章「{chapter_title}」的试写文本。"
            "志怪语感，短句克制，只输出 Markdown 正文。"
        ),
    )
)
register(
    PromptTemplate(
        key="probe.evaluate",
        version=1,
        template=(
            "评估以下文本的质量，只输出 JSON："
            '{{"score": <0-100 整数>, "verdict": "approve"|"revise", "notes": "<短评>"}}\n\n'
            "文本：\n{text}"
        ),
    )
)
register(
    PromptTemplate(
        key="model.repair",
        version=1,
        template=(
            "上一次输出不是合法的 {output_kind} 输出（错误：{error}）。"
            "重新只输出符合要求的完整结果，不要解释。\n\n"
            "原始任务：\n{original_prompt}"
        ),
    )
)
