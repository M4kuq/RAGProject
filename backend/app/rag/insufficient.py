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
_ENGLISH_SCOPE_WORD = (
    r"(?!(?:however|but|and|or|although|though|whereas|while|"
    r"is|are|was|were|be|been|being|do|does|did|has|have|had|"
    r"can|could|will|would|should|must|may|might|shall|because|"
    r"therefore|thus|require|requires|use|uses|contain|contains|"
    r"provide|provides|include|includes|state|states|say|says|"
    r"show|shows|indicate|indicates|report|reports|return|returns)\b)"
    r"[a-z0-9][a-z0-9'_-]*"
)
_ENGLISH_SCOPE_PHRASE = rf"{_ENGLISH_SCOPE_WORD}(?:\s+{_ENGLISH_SCOPE_WORD}){{0,7}}"
_ENGLISH_TRAILING_SCOPE_RE = (
    rf"(?:\s+(?:"
    rf"to\s+(?:(?:answer|respond\s+to)(?:\s+(?:the|this)\s+question)?|{_ENGLISH_SCOPE_PHRASE})"
    rf"|(?:for|about|regarding|concerning|on|in|within)\s+{_ENGLISH_SCOPE_PHRASE}"
    rf"))?"
)
_STANDALONE_ENGLISH_INSUFFICIENT_RE = re.compile(
    rf"(?:"
    rf"(?:there\s+is\s+|there's\s+)?"
    rf"(?:insufficient|not\s+enough|no\s+sufficient|no\s+usable)"
    rf"\s+(?:evidence|context)"
    rf"(?:\s+(?:in|from|within)\s+(?:the\s+)?(?:retrieved\s+)?"
    rf"(?:documents?|context|citations?))?"
    rf"{_ENGLISH_TRAILING_SCOPE_RE}"
    rf"|"
    rf"(?:the\s+)?(?:retrieved\s+)?(?:documents?|context|citations?)"
    rf"\s+(?:do|does)\s+not\s+(?:contain|provide|include)"
    rf"\s+(?:enough|sufficient|usable)\s+(?:evidence|context)"
    rf"{_ENGLISH_TRAILING_SCOPE_RE}"
    rf")",
    re.IGNORECASE,
)
_JAPANESE_SCOPE_CHAR = (
    r"(?!(?:しかし|ただし|一方))"
    r"[^。、．.，,！？!?:：;；\"'「」『』（）()【】\[\]\s]"
)
_JAPANESE_SCOPE_RE = (
    rf"(?:{_JAPANESE_SCOPE_CHAR}{{1,40}}"
    rf"(?:について|に関して|に対して|のための)[、,]?\s*)?"
)
_JAPANESE_SOURCE_RE = r"(?:検索された(?:文書|引用)(?:には|では)[、,]?\s*)?"
_STANDALONE_JAPANESE_INSUFFICIENT_RE = re.compile(
    rf"(?:"
    rf"{_JAPANESE_SOURCE_RE}"
    rf"{_JAPANESE_SCOPE_RE}"
    rf"(?:(?:この質問に(?:直接)?(?:答える|回答する)|この質問への回答をする)ための\s*)?"
    rf"十分な(?:情報|根拠|エビデンス)が(?:ない|ありません|存在しません)"
    rf"|"
    rf"{_JAPANESE_SOURCE_RE}"
    rf"{_JAPANESE_SCOPE_RE}"
    rf"(?:情報|根拠|エビデンス)が不足(?:している|しています)?"
    rf"|"
    rf"{_JAPANESE_SOURCE_RE}"
    rf"{_JAPANESE_SCOPE_RE}"
    rf"(?:この質問への)?回答を確定できません"
    rf")"
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
