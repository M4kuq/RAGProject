from __future__ import annotations

import re

INJECTION_PATTERN_REASON_CODE = "injection_pattern_detected"

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
)


def detect_injection_patterns(text: str) -> list[str]:
    """Return the names of prompt-injection patterns matched in ``text``.

    Observability only: the caller records the matches but must not alter
    retrieval or generation behavior based on the result. The returned list is
    de-duplicated and preserves the declared pattern order.
    """
    if not text:
        return []
    matched: list[str] = []
    for name, pattern in _PATTERNS:
        if pattern.search(text):
            matched.append(name)
    return matched
