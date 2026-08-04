from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.rag.retrieval import RetrievalFilters, _payload_matches_filters, _qdrant_filter
from app.schemas.documents import DocumentSecurityReviewRequest


def _payload(*, security_review_status: object = "approved") -> dict[str, object]:
    return {
        "is_active": True,
        "document_version_status": "ready",
        "logical_document_status": "active",
        "modality": "text",
        "security_review_status": security_review_status,
    }


def test_qdrant_security_review_filter_is_legacy_compatible_and_fail_closed() -> None:
    filters = RetrievalFilters()
    qdrant_filter = _qdrant_filter(filters)
    assert qdrant_filter["must_not"] == [
        {
            "key": "security_review_status",
            "match": {"any": ["pending", "quarantined"]},
        }
    ]
    assert _payload_matches_filters(_payload(), filters) is True
    legacy_payload = _payload()
    legacy_payload.pop("security_review_status")
    assert _payload_matches_filters(legacy_payload, filters) is True
    assert _payload_matches_filters(_payload(security_review_status="pending"), filters) is False
    assert (
        _payload_matches_filters(_payload(security_review_status="quarantined"), filters) is False
    )
    assert _payload_matches_filters(_payload(security_review_status="unknown"), filters) is False


def test_security_review_reason_codes_are_bound_to_status() -> None:
    approved = DocumentSecurityReviewRequest(
        status="approved",
        reason_code="admin_review_passed",
    )
    quarantined = DocumentSecurityReviewRequest(
        status="quarantined",
        reason_code="prompt_injection_detected",
    )
    assert approved.status == "approved"
    assert quarantined.status == "quarantined"
    with pytest.raises(ValidationError):
        DocumentSecurityReviewRequest(
            status="approved",
            reason_code="operator_quarantine",
        )
