from __future__ import annotations

import base64
import binascii
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, cast

INJECTION_PATTERN_REASON_CODE = "injection_pattern_detected"
INJECTION_CONTEXT_QUARANTINED_REASON_CODE = "injection_context_quarantined"
INJECTION_ALL_CONTEXT_QUARANTINED_REASON_CODE = "injection_all_context_quarantined"
INJECTION_USER_BLOCKED_REASON_CODE = "injection_user_blocked"
INJECTION_TOOL_RESULT_QUARANTINED_REASON_CODE = "injection_tool_result_quarantined"

InjectionPolicyName = Literal[
    "observe_only",
    "quarantine_context",
    "block_user_quarantine_context",
]

_BASE64_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9+/_=-])([A-Za-z0-9+/_-]{24,}={0,2})(?![A-Za-z0-9+/_=-])"
)
_MAX_DETECTION_CHARS = 20_000
_MAX_BASE64_CANDIDATES = 8
_MAX_BASE64_DECODED_BYTES = 4_000

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
    (
        "ignore_previous_instructions_zh",
        re.compile(r"忽略(?:之前|先前|以上|所有)的?(?:指令|指示|说明)"),
    ),
    (
        "output_exactly_zh",
        re.compile(r"(?:准确|原样)(?:输出|返回|回复)"),
    ),
    (
        "ignore_previous_instructions_ko",
        re.compile(r"(?:이전|위의|모든)\s*(?:지시|명령)(?:를|을)?\s*무시"),
    ),
    (
        "output_exactly_ko",
        re.compile(r"그대로\s*(?:출력|반환|응답)"),
    ),
    (
        "ignore_previous_instructions_es",
        re.compile(
            r"(?i)\bignora\s+(?:las\s+)?(?:instrucciones|indicaciones)\s+(?:anteriores|previas)\b"
        ),
    ),
    (
        "output_exactly_es",
        re.compile(r"(?i)\b(?:imprime|devuelve|escribe)\s+exactamente\b"),
    ),
    (
        "unauthorized_tool_request",
        re.compile(
            r"(?im)^\s*(?:please\s+)?(?:call|invoke|run|use)\b.{0,32}"
            r"\b(?:admin|write|delete|shell|payment|external)\s+(?:tool|function|action)\b"
        ),
    ),
    (
        "model_tier_escalation_request",
        re.compile(
            r"(?im)^\s*(?:please\s+)?(?:switch|upgrade|escalate|route|send|use|select)\b.{0,36}"
            r"\b(?:plus|premium|expensive|highest[- ]cost)\s+(?:model|tier)\b"
        ),
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
        "ignore_previous_instructions_zh",
        "ignore_previous_instructions_ko",
        "ignore_previous_instructions_es",
    }
)
_ACTION_PATTERN_NAMES = frozenset(
    {
        "reveal_system_prompt",
        "reveal_system_prompt_ja",
        "output_exactly",
        "output_exactly_ja",
        "output_exactly_zh",
        "output_exactly_ko",
        "output_exactly_es",
        "unauthorized_tool_request",
        "model_tier_escalation_request",
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
    normalized_variants = _detection_variants(text)
    matched: list[str] = []
    for name, pattern in _PATTERNS:
        if any(pattern.search(candidate) for candidate in normalized_variants):
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
    normalized = unicodedata.normalize("NFKC", value[:_MAX_DETECTION_CHARS])
    return "".join(character for character in normalized if unicodedata.category(character) != "Cf")


def _detection_variants(value: str) -> tuple[str, ...]:
    normalized = _normalize_for_detection(value)
    variants = [normalized]
    attempts = 0
    for _depth in range(2):
        new_variants: list[str] = []
        for candidate in tuple(variants):
            for encoded in _BASE64_TOKEN_RE.findall(candidate):
                if attempts >= _MAX_BASE64_CANDIDATES:
                    return tuple(variants)
                attempts += 1
                decoded = _decode_base64_text(encoded)
                if decoded and decoded not in variants and decoded not in new_variants:
                    new_variants.append(decoded)
        if not new_variants:
            break
        variants.extend(new_variants)
    return tuple(variants)


def _decode_base64_text(value: str) -> str | None:
    padding = "=" * ((4 - len(value) % 4) % 4)
    try:
        decoded = base64.b64decode(value + padding, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError):
        return None
    if not decoded or len(decoded) > _MAX_BASE64_DECODED_BYTES:
        return None
    try:
        text = decoded.decode("utf-8")
    except UnicodeDecodeError:
        return None
    printable = sum(character.isprintable() or character.isspace() for character in text)
    if printable / len(text) < 0.90:
        return None
    return _normalize_for_detection(text)
