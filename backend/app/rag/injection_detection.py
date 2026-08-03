from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, cast

INJECTION_PATTERN_REASON_CODE = "injection_pattern_detected"
INJECTION_CONTEXT_QUARANTINED_REASON_CODE = "injection_context_quarantined"
INJECTION_ALL_CONTEXT_QUARANTINED_REASON_CODE = "injection_all_context_quarantined"
INJECTION_USER_BLOCKED_REASON_CODE = "injection_user_blocked"

InjectionPolicyName = Literal[
    "observe_only",
    "quarantine_context",
    "block_user_quarantine_context",
]

_ZERO_WIDTH_RE = re.compile("[\u200b-\u200f\u2060\ufeff]")

# Small, precise pattern set. Each entry maps a stable pattern name (used as the
# matched-pattern identifier) to a compiled, case-insensitive regular expression.
# Patterns are intentionally narrow to avoid false positives on legitimate
# documents (e.g. text mentioning "system architecture" or a "previous chapter").
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "ignore_previous_instructions",
        re.compile(r"(?i)\bignore\s+(?:all|previous)\s+instructions\b"),
    ),
    (
        "disregard_instructions",
        re.compile(r"(?i)\bdisregard\s+(?:the|all|previous)\s+instructions\b"),
    ),
    (
        "role_marker_system",
        re.compile(r"(?im)^\s*system:"),
    ),
    (
        "role_marker_assistant",
        re.compile(r"(?im)^\s*assistant:"),
    ),
    (
        "you_are_now",
        re.compile(r"(?i)\byou\s+are\s+now\b"),
    ),
    (
        "new_instructions",
        re.compile(r"(?i)\bnew\s+instructions:"),
    ),
    (
        "ignore_previous_instructions_ja",
        re.compile(r"(?:以前|これまで|上記|上)の指示を無視"),
    ),
    (
        "role_marker_system_ja",
        re.compile(r"(?m)^\s*システム\s*[:：]"),
    ),
    (
        "reveal_system_prompt",
        re.compile(
            r"(?i)\b(?:reveal|show|print|leak|expose)\b.{0,40}"
            r"\b(?:system|hidden)\s+(?:prompt|instructions?)\b"
        ),
    ),
    (
        "reveal_system_prompt_ja",
        re.compile(r"(?:システム|隠された?)プロンプト.{0,20}(?:表示|開示|出力|漏洩)"),
    ),
    (
        "output_exactly",
        re.compile(r"(?i)\b(?:output|print|return|write)\s+exactly\b"),
    ),
    (
        "output_exactly_ja",
        re.compile(r"(?:正確に|そのまま).{0,16}(?:出力|表示|返答)"),
    ),
)

_OVERRIDE_PATTERN_NAMES = frozenset(
    {
        "ignore_previous_instructions",
        "disregard_instructions",
        "role_marker_system",
        "role_marker_assistant",
        "you_are_now",
        "new_instructions",
        "ignore_previous_instructions_ja",
        "role_marker_system_ja",
    }
)
_ACTION_PATTERN_NAMES = frozenset(
    {
        "reveal_system_prompt",
        "reveal_system_prompt_ja",
        "output_exactly",
        "output_exactly_ja",
    }
)


@dataclass(frozen=True)
class ContextInjectionPolicyDecision:
    policy: InjectionPolicyName
    matched_patterns_by_index: tuple[tuple[str, ...], ...]
    allowed_indices: tuple[int, ...]
    quarantined_indices: tuple[int, ...]
    reason_codes: tuple[str, ...]

    @property
    def all_context_quarantined(self) -> bool:
        return bool(self.matched_patterns_by_index) and not self.allowed_indices


@dataclass(frozen=True)
class UserInjectionPolicyDecision:
    policy: InjectionPolicyName
    matched_patterns: tuple[str, ...]
    blocked: bool
    reason_codes: tuple[str, ...]


def detect_injection_patterns(text: str) -> list[str]:
    """Return the names of prompt-injection patterns matched in ``text``.

    Observability only: the caller records the matches but must not alter
    retrieval or generation behavior based on the result. The returned list is
    de-duplicated and preserves the declared pattern order.
    """
    if not text:
        return []
    normalized = _normalize_for_detection(text)
    matched: list[str] = []
    for name, pattern in _PATTERNS:
        if pattern.search(normalized):
            matched.append(name)
    return matched


def evaluate_context_injection_policy(
    texts: Sequence[str],
    *,
    policy: InjectionPolicyName,
) -> ContextInjectionPolicyDecision:
    normalized_policy = _validated_policy(policy)
    matched = tuple(tuple(detect_injection_patterns(text)) for text in texts)
    detected_indices = tuple(index for index, names in enumerate(matched) if names)
    quarantine = normalized_policy in {
        "quarantine_context",
        "block_user_quarantine_context",
    }
    quarantined_indices = detected_indices if quarantine else ()
    quarantined = set(quarantined_indices)
    allowed_indices = tuple(index for index in range(len(texts)) if index not in quarantined)

    reason_codes: list[str] = []
    if detected_indices:
        reason_codes.append(INJECTION_PATTERN_REASON_CODE)
    if quarantined_indices:
        reason_codes.append(INJECTION_CONTEXT_QUARANTINED_REASON_CODE)
    if texts and not allowed_indices:
        reason_codes.append(INJECTION_ALL_CONTEXT_QUARANTINED_REASON_CODE)
    return ContextInjectionPolicyDecision(
        policy=normalized_policy,
        matched_patterns_by_index=matched,
        allowed_indices=allowed_indices,
        quarantined_indices=quarantined_indices,
        reason_codes=tuple(reason_codes),
    )


def evaluate_user_injection_policy(
    text: str,
    *,
    policy: InjectionPolicyName,
) -> UserInjectionPolicyDecision:
    normalized_policy = _validated_policy(policy)
    matched = tuple(detect_injection_patterns(text))
    names = set(matched)
    composite_attack = bool(names.intersection(_OVERRIDE_PATTERN_NAMES)) and bool(
        names.intersection(_ACTION_PATTERN_NAMES)
    )
    blocked = normalized_policy == "block_user_quarantine_context" and composite_attack
    reason_codes: list[str] = []
    if matched:
        reason_codes.append(INJECTION_PATTERN_REASON_CODE)
    if blocked:
        reason_codes.append(INJECTION_USER_BLOCKED_REASON_CODE)
    return UserInjectionPolicyDecision(
        policy=normalized_policy,
        matched_patterns=matched,
        blocked=blocked,
        reason_codes=tuple(reason_codes),
    )


def _validated_policy(value: str) -> InjectionPolicyName:
    normalized = value.strip().lower()
    if normalized not in {
        "observe_only",
        "quarantine_context",
        "block_user_quarantine_context",
    }:
        raise ValueError("invalid injection policy")
    return cast(InjectionPolicyName, normalized)


def _normalize_for_detection(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return _ZERO_WIDTH_RE.sub("", normalized)
