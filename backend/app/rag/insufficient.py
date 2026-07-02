from __future__ import annotations

import re

_CITATION_MARKER_RE = re.compile(r"\[(\d{1,6})\]")

INSUFFICIENT_EVIDENCE_ANSWER_TEMPLATES = (
    "検索された文書には、この質問に答えるための十分な根拠がありません",
    "検索された文書には、この質問に直接答えるための十分な根拠がありません",
    "検索された引用では、この質問への回答を確定できません",
    "insufficient evidence",
    "insufficient context",
    "not enough evidence",
    "not enough context",
    "no sufficient evidence",
    "no usable context",
)


def is_insufficient_evidence_answer(answer_text: str) -> bool:
    compact_answer = compact_insufficient_evidence_text(answer_text)
    return any(
        compact_answer == compact_insufficient_evidence_text(template)
        for template in INSUFFICIENT_EVIDENCE_ANSWER_TEMPLATES
    )


def compact_insufficient_evidence_text(value: str) -> str:
    without_markers = _CITATION_MARKER_RE.sub("", value.lower())
    return re.sub(r"[\s。、．.，,！？!?:：;；「」『』（）()【】]+", "", without_markers)
