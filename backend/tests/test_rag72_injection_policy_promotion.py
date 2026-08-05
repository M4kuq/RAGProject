import re
from pathlib import Path

import pytest

from app.core.config import Settings

ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = ROOT / "docker-compose.internet-facing-security.yml"
ROLLBACK_PATH = ROOT / "docker-compose.internet-facing-policy-rollback.yml"


@pytest.mark.skipif(
    not PROFILE_PATH.is_file(),
    reason="internet-facing compose profile is not available in backend image",
)
def test_internet_facing_profile_uses_promoted_injection_policy() -> None:
    profile = PROFILE_PATH.read_text(encoding="utf-8")

    assert re.search(
        r"RAG_INJECTION_POLICY:\s*block_user_quarantine_context",
        profile,
    )
    assert profile.count("APP_ENV: production") == 2
    assert profile.count('SESSION_COOKIE_SECURE: "true"') == 2


@pytest.mark.skipif(
    not ROLLBACK_PATH.is_file(),
    reason="internet-facing rollback profile is not available in backend image",
)
def test_internet_facing_profile_has_explicit_policy_rollback() -> None:
    rollback = ROLLBACK_PATH.read_text(encoding="utf-8")

    assert rollback.count("RAG_INJECTION_POLICY: observe_only") == 2


def test_local_and_test_default_remains_observe_only() -> None:
    assert Settings(_env_file=None, app_env="test").rag_injection_policy == "observe_only"

    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "RAG_INJECTION_POLICY" not in compose
