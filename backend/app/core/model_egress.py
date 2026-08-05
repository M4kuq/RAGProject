from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, NoReturn

if TYPE_CHECKING:
    from app.core.config import Settings

logger = logging.getLogger(__name__)

EgressPolicy = Literal["deny", "mask", "allow"]
EgressAction = Literal["blocked", "masked", "allowed", "local_bypass"]

EXTERNAL_MODEL_PROVIDERS = frozenset(
    {
        "anthropic",
        "bedrock",
        "gemini",
        "nvidia",
        "openai",
        "qwen",
    }
)
LOCAL_MODEL_PROVIDERS = frozenset({"fake", "lmstudio", "local", "ollama"})
MAX_MODEL_EGRESS_FIELD_CHARS = 1_000_000
MAX_MODEL_EGRESS_PAYLOAD_CHARS = 2_000_000
MODEL_EGRESS_DATA_CLASSES = frozenset(
    {
        "document_content",
        "masked_personal_data",
        "response_schema",
        "retrieval_metadata",
        "retrieved_context",
        "system_instruction",
        "task_instruction",
        "tool_result",
        "user_question",
    }
)
USER_CONSENT_REQUIRED_EGRESS_PURPOSES = frozenset(
    {
        "agentic_strategy_planner",
        "embedding_query",
        "generation",
        "llm_tool_planner",
        "rerank",
    }
)

_UNMASKABLE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private_key_material",
        re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----", re.IGNORECASE),
    ),
    (
        "bearer_token",
        re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE),
    ),
    (
        "credential_assignment",
        re.compile(
            r"\b(?:api[_ -]?key|access[_ -]?token|secret|password|passwd|"
            r"session(?:[_ -]?(?:id|token))?|cookie)\b"
            r"\s*[:=]\s*[^\s,;]{4,}",
            re.IGNORECASE,
        ),
    ),
    (
        "aws_access_key",
        re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    ),
    (
        "high_entropy_blob",
        re.compile(r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{80,}={0,2}(?![A-Za-z0-9+/=])"),
    ),
    (
        "jwt_token",
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    ),
    (
        "provider_token",
        re.compile(
            r"\b(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|"
            r"github_pat_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{20,}|"
            r"AIza[A-Za-z0-9_-]{20,})\b"
        ),
    ),
)
_RESERVED_PLACEHOLDER_PATTERN = re.compile(r"\[PII_[A-Z0-9_]+_\d+\]")

_EMAIL_PATTERN = re.compile(
    r"(?<![A-Za-z0-9.!#$%&'*+/=?^_`{|}~-])"
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+"
)
_PHONE_PATTERN = re.compile(r"(?<![A-Za-z0-9])\+?\d[\d ().-]{7,}\d(?![A-Za-z0-9])")
_JAPANESE_POSTAL_CODE_PATTERN = re.compile(r"(?<!\d)〒?\s*\d{3}-\d{4}(?!\d)")
_IPV4_PATTERN = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
_CARD_PATTERN = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
_JAPANESE_ADDRESS_PATTERN = re.compile(
    r"(?:東京都|北海道|(?:大阪|京都)府|(?:神奈川|和歌山|鹿児島|[一-龥]{2,3})県)"
    r"[^\s,，。;；\n]{2,80}"
)
_STREET_ADDRESS_PATTERN = re.compile(
    r"\b\d{1,6}\s+(?:[A-Za-z][A-Za-z.'’-]*\s+){1,6}"
    r"(?:Street|St|Road|Rd|Avenue|Ave|Boulevard|Blvd|Lane|Ln|Drive|Dr|Way)\b"
    r"(?:\s*,?\s*(?:Apt|Apartment|Suite|Unit|#)\s*[A-Za-z0-9-]+)?",
    re.IGNORECASE,
)
_LABELED_NAME_PATTERN = re.compile(
    r"(?:full\s+name|customer\s+name|employee\s+name|name|氏名|名前|担当者)"
    r"\s*[:：=]\s*([^,，;；\n]{2,80})",
    re.IGNORECASE,
)
_LABELED_ADDRESS_PATTERN = re.compile(
    r"(?:street\s+address|mailing\s+address|address|住所|所在地)"
    r"\s*[:：=]\s*([^;；\n]{4,160})",
    re.IGNORECASE,
)
_LABELED_IDENTIFIER_PATTERN = re.compile(
    r"(?:social\s+security(?:\s+number)?|ssn|passport(?:\s+number)?|"
    r"customer\s+id|employee\s+id|user\s+id|顧客\s*(?:id|ID|番号)|"
    r"社員\s*(?:id|ID|番号)|個人番号|マイナンバー|旅券番号)"
    r"\s*[:：=]\s*([A-Za-z0-9][A-Za-z0-9 _.-]{2,63})",
    re.IGNORECASE,
)

_ENTITY_PRIORITY = {
    "GOVERNMENT_ID": 90,
    "PAYMENT_CARD": 80,
    "EMAIL": 70,
    "PHONE": 60,
    "IP_ADDRESS": 50,
    "POSTAL_CODE": 45,
    "ADDRESS": 40,
    "PERSON_NAME": 30,
}


class ModelEgressBlockedError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__("External model egress was blocked by policy.")
        self.reason_code = reason_code


@dataclass(frozen=True)
class ModelEgressGovernanceRule:
    provider: str
    model: str
    purpose: str
    allowed_data_classes: frozenset[str]
    allowed_regions: frozenset[str]
    retention_days: int
    training_allowed: bool
    user_consent_required: bool


@dataclass(frozen=True)
class ModelEgressRequestContext:
    authenticated_user: bool = False
    user_consent_granted: bool = False


_MODEL_EGRESS_REQUEST_CONTEXT: ContextVar[ModelEgressRequestContext | None] = ContextVar(
    "model_egress_request_context",
    default=None,
)


@contextmanager
def model_egress_request_scope(
    *,
    authenticated_user: bool,
    user_consent_granted: bool,
) -> Iterator[None]:
    """Bind non-content authorization facts to the current request only."""

    token = _MODEL_EGRESS_REQUEST_CONTEXT.set(
        ModelEgressRequestContext(
            authenticated_user=authenticated_user,
            user_consent_granted=user_consent_granted,
        )
    )
    try:
        yield
    finally:
        _MODEL_EGRESS_REQUEST_CONTEXT.reset(token)


@dataclass(frozen=True)
class ModelEgressAudit:
    provider: str
    purpose: str
    policy: EgressPolicy
    action: EgressAction
    policy_version: str = "pii-v1"
    data_classes: tuple[str, ...] = ()
    entity_counts: tuple[tuple[str, int], ...] = ()
    reason_codes: tuple[str, ...] = ()

    @property
    def masked_count(self) -> int:
        return sum(count for _, count in self.entity_counts)

    def log_fields(self) -> dict[str, object]:
        return {
            "model_egress_provider": self.provider,
            "model_egress_purpose": self.purpose,
            "model_egress_policy": self.policy,
            "model_egress_policy_version": self.policy_version,
            "model_egress_action": self.action,
            "model_egress_data_classes": list(self.data_classes),
            "model_egress_entity_types": [name for name, _ in self.entity_counts],
            "model_egress_entity_counts": dict(self.entity_counts),
            "model_egress_masked_count": self.masked_count,
            "model_egress_reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True)
class ProtectedTexts:
    texts: tuple[str, ...]
    audit: ModelEgressAudit


@dataclass(frozen=True)
class ProtectedPayload:
    payload: object
    audit: ModelEgressAudit


@dataclass(frozen=True)
class _EntitySpan:
    start: int
    end: int
    entity_type: str
    value: str


class ModelEgressGuard:
    """Protect text immediately before it crosses an external model boundary.

    The guard deliberately has no dependency on a remote detector. Detection is
    deterministic and local, and a provider call is never made when the policy is
    ambiguous or unsupported sensitive material is present.
    """

    def __init__(
        self,
        *,
        policy: EgressPolicy,
        allowed_providers: Sequence[str],
        pii_masking_enabled: bool,
        governance_enabled: bool = False,
        governance_policy_version: str = "pii-v1",
        governance_rules: Sequence[ModelEgressGovernanceRule] = (),
        provider_regions: Mapping[str, str] | None = None,
        max_retention_days: int = 0,
    ) -> None:
        self.policy = policy
        self.allowed_providers = frozenset(
            provider.strip().lower() for provider in allowed_providers
        )
        self.pii_masking_enabled = pii_masking_enabled
        self.governance_enabled = governance_enabled
        self.governance_policy_version = governance_policy_version.strip().lower()
        self.provider_regions = {
            provider.strip().lower(): region.strip().lower()
            for provider, region in (provider_regions or {}).items()
        }
        self.max_retention_days = max_retention_days
        self.governance_rules: dict[tuple[str, str, str], ModelEgressGovernanceRule] = {}
        for rule in governance_rules:
            key = (rule.provider.strip().lower(), rule.model.strip(), rule.purpose.strip())
            if key in self.governance_rules:
                raise ValueError("duplicate external model egress governance rule")
            self.governance_rules[key] = rule

    @classmethod
    def from_settings(cls, settings: Settings) -> ModelEgressGuard:
        return cls(
            policy=settings.external_model_egress_policy,
            allowed_providers=settings.external_model_egress_allowed_providers,
            pii_masking_enabled=settings.pii_masking_enabled,
            governance_enabled=settings.external_model_egress_governance_enabled,
            governance_policy_version=(settings.external_model_egress_governance_policy_version),
            governance_rules=[
                ModelEgressGovernanceRule(
                    provider=rule.provider,
                    model=rule.model,
                    purpose=rule.purpose,
                    allowed_data_classes=frozenset(rule.allowed_data_classes),
                    allowed_regions=frozenset(rule.allowed_regions),
                    retention_days=rule.retention_days,
                    training_allowed=rule.training_allowed,
                    user_consent_required=rule.user_consent_required,
                )
                for rule in settings.external_model_egress_rules
            ],
            provider_regions=settings.external_model_egress_provider_regions,
            max_retention_days=settings.external_model_egress_max_retention_days,
        )

    def protect_texts(
        self,
        texts: Sequence[str],
        *,
        provider: str,
        purpose: str,
        model: str | None = None,
        data_classes: Sequence[str] = (),
    ) -> ProtectedTexts:
        normalized_provider = provider.strip().lower()
        normalized_classes = tuple(sorted({item.strip().lower() for item in data_classes}))
        original = tuple(texts)
        if normalized_provider in LOCAL_MODEL_PROVIDERS:
            audit = ModelEgressAudit(
                provider=normalized_provider,
                purpose=purpose,
                policy=self.policy,
                action="local_bypass",
                policy_version=self._audit_policy_version,
                data_classes=normalized_classes,
                reason_codes=("local_provider",),
            )
            return ProtectedTexts(original, audit)
        if normalized_provider not in EXTERNAL_MODEL_PROVIDERS:
            self._block(
                normalized_provider,
                purpose,
                "unknown_provider",
                data_classes=normalized_classes,
            )
        if self.policy == "deny":
            self._block(
                normalized_provider,
                purpose,
                "egress_policy_denied",
                data_classes=normalized_classes,
            )
        if normalized_provider not in self.allowed_providers:
            self._block(
                normalized_provider,
                purpose,
                "provider_not_allowed",
                data_classes=normalized_classes,
            )
        governance_rule = self._authorize_governance(
            provider=normalized_provider,
            model=model,
            purpose=purpose,
            data_classes=normalized_classes,
        )
        if self.policy == "allow":
            if self.governance_enabled:
                self._block(
                    normalized_provider,
                    purpose,
                    "unmasked_external_egress_disallowed",
                    data_classes=normalized_classes,
                )
            audit = ModelEgressAudit(
                provider=normalized_provider,
                purpose=purpose,
                policy=self.policy,
                action="allowed",
                policy_version=self._audit_policy_version,
                data_classes=normalized_classes,
                reason_codes=("explicit_unmasked_allow",),
            )
            self._log(audit)
            return ProtectedTexts(original, audit)
        if not self.pii_masking_enabled:
            self._block(
                normalized_provider,
                purpose,
                "pii_masking_disabled",
                data_classes=normalized_classes,
            )

        if (
            any(len(text) > MAX_MODEL_EGRESS_FIELD_CHARS for text in original)
            or sum(map(len, original)) > MAX_MODEL_EGRESS_PAYLOAD_CHARS
        ):
            self._block(
                normalized_provider,
                purpose,
                "payload_too_large",
                data_classes=normalized_classes,
            )
        for text in original:
            if _contains_unsupported_control(text):
                self._block(
                    normalized_provider,
                    purpose,
                    "unsupported_control_content",
                    data_classes=normalized_classes,
                )
            if _RESERVED_PLACEHOLDER_PATTERN.search(text):
                self._block(
                    normalized_provider,
                    purpose,
                    "reserved_placeholder_collision",
                    data_classes=normalized_classes,
                )
            for reason_code, pattern in _UNMASKABLE_PATTERNS:
                if pattern.search(text):
                    self._block(
                        normalized_provider,
                        purpose,
                        reason_code,
                        data_classes=normalized_classes,
                    )

        all_spans = [_find_entity_spans(text) for text in original]
        placeholders: dict[tuple[str, str], str] = {}
        next_index: Counter[str] = Counter()
        entity_counts: Counter[str] = Counter()
        protected: list[str] = []
        for text, spans in zip(original, all_spans, strict=True):
            for span in spans:
                key = (span.entity_type, span.value)
                if key not in placeholders:
                    next_index[span.entity_type] += 1
                    placeholders[key] = f"[PII_{span.entity_type}_{next_index[span.entity_type]}]"
                entity_counts[span.entity_type] += 1
            protected.append(_replace_spans(text, spans, placeholders))

        sorted_counts = tuple(sorted(entity_counts.items()))
        if governance_rule is not None and sorted_counts:
            governed_classes = set(normalized_classes)
            governed_classes.add("masked_personal_data")
            if "masked_personal_data" not in governance_rule.allowed_data_classes:
                self._block(
                    normalized_provider,
                    purpose,
                    "masked_personal_data_not_allowed",
                    data_classes=tuple(sorted(governed_classes)),
                    entity_counts=sorted_counts,
                )
            reidentification_reason = _reidentification_risk_reason(entity_counts)
            if reidentification_reason is not None:
                self._block(
                    normalized_provider,
                    purpose,
                    reidentification_reason,
                    data_classes=tuple(sorted(governed_classes)),
                    entity_counts=sorted_counts,
                )
            normalized_classes = tuple(sorted(governed_classes))
        audit = ModelEgressAudit(
            provider=normalized_provider,
            purpose=purpose,
            policy=self.policy,
            action="masked" if sorted_counts else "allowed",
            policy_version=self._audit_policy_version,
            data_classes=normalized_classes,
            entity_counts=sorted_counts,
            reason_codes=(
                ("governance_approved", "pii_masked")
                if governance_rule is not None and sorted_counts
                else ("governance_approved", "clean_payload")
                if governance_rule is not None
                else ("pii_masked",)
                if sorted_counts
                else ("clean_payload",)
            ),
        )
        self._log(audit)
        return ProtectedTexts(tuple(protected), audit)

    def protect_payload(
        self,
        payload: object,
        *,
        provider: str,
        purpose: str,
        model: str | None = None,
        data_classes: Sequence[str] = (),
    ) -> ProtectedPayload:
        leaves: list[str] = []
        _collect_string_values(payload, leaves)
        protected = self.protect_texts(
            leaves,
            provider=provider,
            purpose=purpose,
            model=model,
            data_classes=data_classes,
        )
        iterator = iter(protected.texts)
        rebuilt = _replace_string_values(payload, iterator)
        return ProtectedPayload(payload=rebuilt, audit=protected.audit)

    def _authorize_governance(
        self,
        *,
        provider: str,
        model: str | None,
        purpose: str,
        data_classes: tuple[str, ...],
    ) -> ModelEgressGovernanceRule | None:
        if not self.governance_enabled:
            return None
        if model is None or not model.strip():
            self._block(provider, purpose, "model_identity_missing", data_classes=data_classes)
        if not data_classes:
            self._block(provider, purpose, "data_classification_missing")
        if set(data_classes) - MODEL_EGRESS_DATA_CLASSES:
            self._block(
                provider,
                purpose,
                "data_classification_unknown",
                data_classes=data_classes,
            )
        assert model is not None
        rule = self.governance_rules.get((provider, model.strip(), purpose))
        if rule is None:
            self._block(
                provider,
                purpose,
                "governance_rule_missing",
                data_classes=data_classes,
            )
        assert rule is not None
        if not set(data_classes).issubset(rule.allowed_data_classes):
            self._block(
                provider,
                purpose,
                "data_class_not_allowed",
                data_classes=data_classes,
            )
        region = self.provider_regions.get(provider)
        if region is None:
            self._block(
                provider,
                purpose,
                "provider_region_unknown",
                data_classes=data_classes,
            )
        if region not in rule.allowed_regions:
            self._block(
                provider,
                purpose,
                "provider_region_not_allowed",
                data_classes=data_classes,
            )
        if rule.retention_days > self.max_retention_days:
            self._block(
                provider,
                purpose,
                "retention_policy_incompatible",
                data_classes=data_classes,
            )
        if rule.training_allowed:
            self._block(
                provider,
                purpose,
                "training_policy_incompatible",
                data_classes=data_classes,
            )
        if purpose in USER_CONSENT_REQUIRED_EGRESS_PURPOSES and not rule.user_consent_required:
            self._block(
                provider,
                purpose,
                "consent_policy_incompatible",
                data_classes=data_classes,
            )
        request_context = _MODEL_EGRESS_REQUEST_CONTEXT.get() or ModelEgressRequestContext()
        if rule.user_consent_required and not request_context.authenticated_user:
            self._block(
                provider,
                purpose,
                "authenticated_user_required",
                data_classes=data_classes,
            )
        if rule.user_consent_required and not request_context.user_consent_granted:
            self._block(
                provider,
                purpose,
                "user_consent_required",
                data_classes=data_classes,
            )
        return rule

    @property
    def _audit_policy_version(self) -> str:
        return self.governance_policy_version if self.governance_enabled else "pii-v1"

    def _block(
        self,
        provider: str,
        purpose: str,
        reason_code: str,
        *,
        data_classes: Sequence[str] = (),
        entity_counts: tuple[tuple[str, int], ...] = (),
    ) -> NoReturn:
        audit = ModelEgressAudit(
            provider=provider,
            purpose=purpose,
            policy=self.policy,
            action="blocked",
            policy_version=self._audit_policy_version,
            data_classes=tuple(data_classes),
            entity_counts=entity_counts,
            reason_codes=(reason_code,),
        )
        self._log(audit)
        raise ModelEgressBlockedError(reason_code)

    @staticmethod
    def _log(audit: ModelEgressAudit) -> None:
        logger.info("model egress policy decision", extra=audit.log_fields())


def _reidentification_risk_reason(entity_counts: Mapping[str, int]) -> str | None:
    entity_types = set(entity_counts)
    if entity_types & {"GOVERNMENT_ID", "PAYMENT_CARD"}:
        return "unsupported_sensitive_identifier"
    if "PERSON_NAME" in entity_types and "ADDRESS" in entity_types:
        return "reidentification_risk"
    if len(entity_types) >= 3:
        return "reidentification_risk"
    return None


def _find_entity_spans(text: str) -> list[_EntitySpan]:
    candidates: list[_EntitySpan] = []
    _append_matches(candidates, text, _LABELED_IDENTIFIER_PATTERN, "GOVERNMENT_ID", group=1)
    _append_matches(candidates, text, _CARD_PATTERN, "PAYMENT_CARD", validator=_valid_card)
    _append_matches(candidates, text, _EMAIL_PATTERN, "EMAIL")
    _append_matches(candidates, text, _PHONE_PATTERN, "PHONE", validator=_valid_phone)
    _append_matches(candidates, text, _IPV4_PATTERN, "IP_ADDRESS", validator=_valid_ipv4)
    _append_matches(candidates, text, _JAPANESE_POSTAL_CODE_PATTERN, "POSTAL_CODE")
    _append_matches(candidates, text, _LABELED_ADDRESS_PATTERN, "ADDRESS", group=1)
    _append_matches(candidates, text, _JAPANESE_ADDRESS_PATTERN, "ADDRESS")
    _append_matches(candidates, text, _STREET_ADDRESS_PATTERN, "ADDRESS")
    _append_matches(candidates, text, _LABELED_NAME_PATTERN, "PERSON_NAME", group=1)
    candidates.sort(
        key=lambda span: (
            -_ENTITY_PRIORITY[span.entity_type],
            span.start,
            -(span.end - span.start),
        )
    )
    selected: list[_EntitySpan] = []
    for candidate in candidates:
        if any(candidate.start < span.end and span.start < candidate.end for span in selected):
            continue
        selected.append(candidate)
    return sorted(selected, key=lambda span: span.start)


def _append_matches(
    target: list[_EntitySpan],
    text: str,
    pattern: re.Pattern[str],
    entity_type: str,
    *,
    group: int = 0,
    validator: Callable[[str], bool] | None = None,
) -> None:
    for match in pattern.finditer(text):
        value = match.group(group).strip()
        start, end = match.span(group)
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        value = text[start:end]
        if not value:
            continue
        if validator is not None and not validator(value):
            continue
        target.append(_EntitySpan(start=start, end=end, entity_type=entity_type, value=value))


def _replace_spans(
    text: str,
    spans: Sequence[_EntitySpan],
    placeholders: Mapping[tuple[str, str], str],
) -> str:
    result = text
    for span in reversed(spans):
        result = (
            result[: span.start] + placeholders[(span.entity_type, span.value)] + result[span.end :]
        )
    return result


def _valid_phone(value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    return 9 <= len(digits) <= 15 and len(set(digits)) > 1


def _valid_ipv4(value: str) -> bool:
    parts = value.split(".")
    return len(parts) == 4 and all(part.isdigit() and 0 <= int(part) <= 255 for part in parts)


def _valid_card(value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    if not 13 <= len(digits) <= 19 or len(set(digits)) == 1:
        return False
    checksum = 0
    parity = len(digits) % 2
    for index, character in enumerate(digits):
        number = int(character)
        if index % 2 == parity:
            number *= 2
            if number > 9:
                number -= 9
        checksum += number
    return checksum % 10 == 0


def _contains_unsupported_control(text: str) -> bool:
    return any(ord(character) < 32 and character not in "\n\r\t" for character in text)


def _collect_string_values(value: object, target: list[str]) -> None:
    if isinstance(value, str):
        target.append(value)
    elif isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str):
                target.append(key)
            _collect_string_values(item, target)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _collect_string_values(item, target)


def _replace_string_values(value: object, replacements: Iterator[str]) -> object:
    if isinstance(value, str):
        return next(replacements)
    if isinstance(value, Mapping):
        rebuilt: dict[object, object] = {}
        for key, item in value.items():
            protected_key = next(replacements) if isinstance(key, str) else key
            rebuilt[protected_key] = _replace_string_values(item, replacements)
        return rebuilt
    if isinstance(value, list):
        return [_replace_string_values(item, replacements) for item in value]
    if isinstance(value, tuple):
        return tuple(_replace_string_values(item, replacements) for item in value)
    return value
