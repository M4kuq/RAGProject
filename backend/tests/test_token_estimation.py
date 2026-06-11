from __future__ import annotations

import math

from app.core.tokens import estimate_tokens


def test_empty_string_is_zero() -> None:
    assert estimate_tokens("") == 0


def test_pure_ascii_uses_ceil_chars_over_four() -> None:
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 2
    assert estimate_tokens("x" * 17) == math.ceil(17 / 4)
    for length in range(1, 33):
        assert estimate_tokens("a" * length) == math.ceil(length / 4)


def test_pure_japanese_is_roughly_one_token_per_char() -> None:
    text = "これはテストです"
    assert len(text) == 8
    assert estimate_tokens(text) == 8


def test_mixed_text_combines_ascii_and_non_ascii() -> None:
    # 4 ASCII chars -> ceil(4/4) = 1 token; 2 non-ASCII chars -> 2 tokens.
    assert estimate_tokens("abcdあい") == 1 + 2
    # 5 ASCII chars -> ceil(5/4) = 2 tokens; 3 non-ASCII chars -> 3 tokens.
    assert estimate_tokens("hello日本語") == 2 + 3


def test_reexport_from_context_budget_resolves_to_same_callable() -> None:
    from app.rag.context_budget import estimate_tokens as reexported

    assert reexported is estimate_tokens
    assert reexported("これはテストです") == 8
