from __future__ import annotations

import re

_CITATION_MARKER_RE = re.compile(r"\[(\d{1,6})\]")
_ANSWER_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"final\s+answer|"
    r"answer|"
    r"drafting\s+the\s+answer|"
    r"drafting\s+the\s+response(?:\s+in\s+japanese)?|"
    r"draft|"
    r"response|"
    r"最終回答|"
    r"回答"
    r")\s*[:：]\s*",
    re.IGNORECASE,
)
_EDGE_NOISE_CHARS = " \t\r\n。、．.，,！？!?:：;；\"'「」『』（）()【】[]"
_STANDALONE_ENGLISH_INSUFFICIENT_RE = re.compile(
    r"(?:"
    r"(?:there\s+is\s+|there's\s+)?"
    r"(?:insufficient|not\s+enough|no\s+sufficient|no\s+usable)"
    r"\s+(?:evidence|context)"
    r"(?:\s+(?:in|from|within)\s+(?:the\s+)?(?:retrieved\s+)?"
    r"(?:documents?|context|citations?))?"
    r"(?:\s+to\s+(?:answer|respond\s+to)(?:\s+(?:the|this)\s+question)?)?"
    r"|"
    r"(?:the\s+)?(?:retrieved\s+)?(?:documents?|context|citations?)"
    r"\s+(?:do|does)\s+not\s+(?:contain|provide|include)"
    r"\s+(?:enough|sufficient|usable)\s+(?:evidence|context)"
    r"(?:\s+to\s+(?:answer|respond\s+to)(?:\s+(?:the|this)\s+question)?)?"
    r")",
    re.IGNORECASE,
)
_STANDALONE_JAPANESE_INSUFFICIENT_RE = re.compile(
    r"(?:"
    r"(?:検索された(?:文書|引用)(?:には|では)[、,]?\s*)?"
    r"(?:(?:この質問に(?:直接)?(?:答える|回答する)|この質問への回答をする)ための\s*)?"
    r"十分な(?:情報|根拠|エビデンス)が(?:ない|ありません|存在しません)"
    r"|"
    r"(?:検索された(?:文書|引用)(?:には|では)[、,]?\s*)?"
    r"(?:情報|根拠|エビデンス)が不足(?:している|しています)?"
    r"|"
    r"(?:検索された(?:文書|引用)(?:には|では)[、,]?\s*)?"
    r"(?:この質問への)?回答を確定できません"
    r")"
)

INSUFFICIENT_EVIDENCE_ANSWER_TEMPLATES = (
    "insufficient evidence",
    "insufficient context",
    "not enough evidence",
    "not enough context",
    "no sufficient evidence",
    "no usable context",
)


def is_insufficient_evidence_answer(answer_text: str) -> bool:
    candidate = standalone_insufficient_evidence_candidate(answer_text)
    if not candidate:
        return False
    compact_answer = compact_insufficient_evidence_text(candidate)
    return (
        bool(_STANDALONE_JAPANESE_INSUFFICIENT_RE.fullmatch(candidate))
        or any(
            compact_answer == compact_insufficient_evidence_text(template)
            for template in INSUFFICIENT_EVIDENCE_ANSWER_TEMPLATES
        )
        or bool(_STANDALONE_ENGLISH_INSUFFICIENT_RE.fullmatch(candidate))
    )


def standalone_insufficient_evidence_candidate(value: str) -> str:
    text = value.lower().replace("\x00", " ")
    while True:
        stripped = _ANSWER_PREFIX_RE.sub("", text, count=1)
        if stripped == text:
            break
        text = stripped
    without_markers = _CITATION_MARKER_RE.sub("", text)
    return " ".join(without_markers.split()).strip(_EDGE_NOISE_CHARS)


def compact_insufficient_evidence_text(value: str) -> str:
    candidate = standalone_insufficient_evidence_candidate(value)
    return re.sub(r"[\s。、．.，,！？!?:：;；「」『』（）()【】]+", "", candidate)
