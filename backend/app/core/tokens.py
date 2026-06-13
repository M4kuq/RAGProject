from __future__ import annotations

import math


def estimate_tokens(text: str) -> int:
    """Estimate the token count of ``text`` with a conservative hybrid heuristic.

    This is intentionally a cheap heuristic, not a real tokenizer. ASCII text is
    counted at roughly four characters per token (the classic chars/4 rule, which
    holds reasonably well for English), while every non-ASCII character is counted
    as ~1 token. Japanese (and other CJK) text is close to one token per character
    under common subword tokenizers, so the English-only chars/4 rule would
    underestimate budgets by up to 4x on such corpora; counting non-ASCII at 1
    token/char keeps the budget conservative for mixed and non-English text.

    Returns 0 for empty input.
    """
    if not text:
        return 0
    ascii_count = sum(1 for char in text if ord(char) < 128)
    non_ascii_count = len(text) - ascii_count
    return int(math.ceil(ascii_count / 4)) + non_ascii_count
