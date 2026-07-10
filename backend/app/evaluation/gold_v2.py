from __future__ import annotations

import hashlib
import re
from enum import StrEnum
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

GOLD_DATASET_SCHEMA_VERSION: Final = "phase3.gold_dataset.v2"
GOLD_SOURCE_CATALOG_SCHEMA_VERSION: Final = "phase3.gold_source_catalog.v1"
GROUNDED_ANSWER_RUBRIC_VERSION: Final = "phase3.grounded_answer_judge.v1"
GROUNDED_ANSWER_PRIMARY_METRIC: Final = "grounded_answer_pass_rate"

_FIXTURE_DIR = Path(__file__).with_name("fixtures")
_DATASET_PATH = _FIXTURE_DIR / "gold_answer_quality_v2.json"
_SOURCE_CATALOG_PATH = _FIXTURE_DIR / "gold_answer_quality_v2_sources.json"
_RUBRIC_PATH = _FIXTURE_DIR / "gold_answer_quality_v2_rubric.json"
_SAFE_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,119}$")
_SAFE_TAG_RE = re.compile(r"^[a-z0-9][a-z0-9_:-]{0,79}$")
_EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_SECRET_VALUE_RE = re.compile(
    r"(?i)(api[_ -]?key|secret|password|credential|token)\s*[:=]\s*\S+"
    r"|bearer\s+[A-Za-z0-9._-]{8,}|sk-[A-Za-z0-9_-]{8,}"
)


class GoldV2ValidationError(RuntimeError):
    pass


class EvidenceRole(StrEnum):
    SUPPORTS_ANSWER = "supports_answer"
    NEAR_MISS = "near_miss"


class JudgeOutcome(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNCERTAIN = "uncertain"
    NOT_APPLICABLE = "not_applicable"


class JudgeDimensionName(StrEnum):
    REQUIRED_FACTS_SUPPORTED = "required_facts_supported"
    CITATION_SUPPORT = "citation_support"
    FORBIDDEN_CLAIMS_ABSENT = "forbidden_claims_absent"
    ABSTENTION_CORRECT = "abstention_correct"
    PROMPT_INJECTION_RESISTED = "prompt_injection_resisted"


class JudgeReasonCode(StrEnum):
    MISSING_REQUIRED_FACT = "missing_required_fact"
    UNSUPPORTED_CLAIM = "unsupported_claim"
    CITATION_MISSING = "citation_missing"
    CITATION_MISMATCH = "citation_mismatch"
    INCORRECT_ABSTENTION = "incorrect_abstention"
    FAILED_TO_ABSTAIN = "failed_to_abstain"
    PROMPT_INJECTION_FOLLOWED = "prompt_injection_followed"
    LOW_CONFIDENCE = "low_confidence"
    JUDGE_UNCERTAIN = "judge_uncertain"


class HumanDisagreementCategory(StrEnum):
    AUXILIARY_FALSE_POSITIVE = "auxiliary_false_positive"
    AUXILIARY_FALSE_NEGATIVE = "auxiliary_false_negative"
    RUBRIC_AMBIGUITY = "rubric_ambiguity"
    GOLD_CASE_DEFECT = "gold_case_defect"


class GoldFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_id: str
    statement: str = Field(min_length=1, max_length=500)

    @field_validator("fact_id")
    @classmethod
    def validate_fact_id(cls, value: str) -> str:
        return _safe_key(value)

    @field_validator("statement")
    @classmethod
    def validate_statement(cls, value: str) -> str:
        return _safe_text(value, max_length=500)


class GoldEvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_key: str
    locator: str = Field(min_length=1, max_length=160)
    fact_ids: list[str] = Field(min_length=1, max_length=12)
    role: EvidenceRole

    @field_validator("source_key")
    @classmethod
    def validate_source_key(cls, value: str) -> str:
        return _safe_key(value)

    @field_validator("locator")
    @classmethod
    def validate_locator(cls, value: str) -> str:
        return _safe_text(value, max_length=160)

    @field_validator("fact_ids")
    @classmethod
    def validate_fact_ids(cls, value: list[str]) -> list[str]:
        normalized = [_safe_key(item) for item in value]
        if len(normalized) != len(set(normalized)):
            raise ValueError("fact_ids must be unique")
        return normalized


class GoldCaseV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    question: str = Field(min_length=1, max_length=2000)
    answerable: bool
    reference_answer: str = Field(min_length=1, max_length=4000)
    required_facts: list[GoldFact] = Field(default_factory=list, max_length=12)
    forbidden_claims: list[str] = Field(min_length=1, max_length=12)
    expected_evidence: list[GoldEvidenceRef] = Field(min_length=1, max_length=12)
    required_citation: bool
    expected_strategy: Literal["hybrid", "agentic_router"]
    tags: list[str] = Field(min_length=5, max_length=16)

    @field_validator("case_id")
    @classmethod
    def validate_case_id(cls, value: str) -> str:
        return _safe_key(value)

    @field_validator("question", "reference_answer")
    @classmethod
    def validate_safe_text(cls, value: str) -> str:
        return _safe_text(value, max_length=4000)

    @field_validator("forbidden_claims")
    @classmethod
    def validate_forbidden_claims(cls, value: list[str]) -> list[str]:
        normalized = [_safe_text(item, max_length=500) for item in value]
        if len(normalized) != len(set(normalized)):
            raise ValueError("forbidden_claims must be unique")
        return normalized

    @field_validator("required_facts")
    @classmethod
    def validate_required_facts(cls, value: list[GoldFact]) -> list[GoldFact]:
        fact_ids = [fact.fact_id for fact in value]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("required_facts must use unique fact_ids")
        return value

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for tag in value:
            safe_tag = " ".join(tag.replace("\x00", " ").split()).lower()
            if not _SAFE_TAG_RE.fullmatch(safe_tag):
                raise ValueError("invalid gold case tag")
            if safe_tag not in normalized:
                normalized.append(safe_tag)
        if len(normalized) != len(value):
            raise ValueError("tags must be unique")
        return normalized

    @model_validator(mode="after")
    def validate_case_contract(self) -> GoldCaseV2:
        expected_answer_tag = "answerable" if self.answerable else "unanswerable"
        unexpected_answer_tag = "unanswerable" if self.answerable else "answerable"
        if expected_answer_tag not in self.tags or unexpected_answer_tag in self.tags:
            raise ValueError("answerable tag does not match answerable")
        hop_tags = {"single_hop", "multi_hop"}.intersection(self.tags)
        if len(hop_tags) != 1:
            raise ValueError("exactly one hop tag is required")
        strategy_tag = f"strategy:{self.expected_strategy}"
        if strategy_tag not in self.tags:
            raise ValueError("strategy tag does not match expected_strategy")
        language_tags = {"language:en", "language:ja"}.intersection(self.tags)
        if len(language_tags) != 1:
            raise ValueError("exactly one language tag is required")

        evidence_roles = {evidence.role for evidence in self.expected_evidence}
        if self.answerable:
            if not self.required_facts:
                raise ValueError("answerable cases require required_facts")
            if not self.required_citation:
                raise ValueError("answerable cases require citations")
            if evidence_roles != {EvidenceRole.SUPPORTS_ANSWER}:
                raise ValueError("answerable evidence must support the answer")
            required_fact_ids = {fact.fact_id for fact in self.required_facts}
            covered_fact_ids = {
                fact_id for evidence in self.expected_evidence for fact_id in evidence.fact_ids
            }
            if required_fact_ids != covered_fact_ids:
                raise ValueError("expected evidence must cover every required fact exactly")
        else:
            if self.required_facts:
                raise ValueError("unanswerable cases must not define required_facts")
            if evidence_roles != {EvidenceRole.NEAR_MISS}:
                raise ValueError("unanswerable evidence must be marked near_miss")

        if "single_hop" in self.tags and len(self.expected_evidence) != 1:
            raise ValueError("single_hop cases require one evidence reference")
        if "multi_hop" in self.tags and len(self.expected_evidence) < 2:
            raise ValueError("multi_hop cases require at least two evidence references")
        if "prompt_injection" in self.tags and not any(
            "instruction" in claim.casefold() for claim in self.forbidden_claims
        ):
            raise ValueError(
                "prompt_injection cases require an instruction-related forbidden claim"
            )
        return self


class GoldBalanceTargets(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_case_count: int = Field(ge=1, le=100)
    answerable: int = Field(ge=0)
    unanswerable: int = Field(ge=0)
    single_hop: int = Field(ge=0)
    multi_hop: int = Field(ge=0)
    hybrid: int = Field(ge=0)
    agentic_router: int = Field(ge=0)
    prompt_injection: int = Field(ge=0)
    language_en: int = Field(ge=0)
    language_ja: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_axes(self) -> GoldBalanceTargets:
        total = self.expected_case_count
        for left, right in (
            (self.answerable, self.unanswerable),
            (self.single_hop, self.multi_hop),
            (self.hybrid, self.agentic_router),
            (self.language_en, self.language_ja),
        ):
            if left + right != total:
                raise ValueError("balance axis does not equal expected_case_count")
        if self.prompt_injection > total:
            raise ValueError("prompt_injection target exceeds expected_case_count")
        return self


class GoldDatasetInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_name: Literal["gold_answer_quality_v2"]
    version: Literal["v2"]
    description: str = Field(min_length=1, max_length=1000)
    primary_metric: Literal["grounded_answer_pass_rate"]
    balance_targets: GoldBalanceTargets

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str) -> str:
        return _safe_text(value, max_length=1000)


class GoldDatasetV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["phase3.gold_dataset.v2"]
    dataset: GoldDatasetInfo
    cases: list[GoldCaseV2] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_dataset_contract(self) -> GoldDatasetV2:
        if len(self.cases) != self.dataset.balance_targets.expected_case_count:
            raise ValueError("case count does not match balance target")
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("case_id values must be unique")
        questions = [" ".join(case.question.casefold().split()) for case in self.cases]
        if len(questions) != len(set(questions)):
            raise ValueError("questions must be unique")
        counts = {
            "answerable": sum(case.answerable for case in self.cases),
            "unanswerable": sum(not case.answerable for case in self.cases),
            "single_hop": sum("single_hop" in case.tags for case in self.cases),
            "multi_hop": sum("multi_hop" in case.tags for case in self.cases),
            "hybrid": sum(case.expected_strategy == "hybrid" for case in self.cases),
            "agentic_router": sum(
                case.expected_strategy == "agentic_router" for case in self.cases
            ),
            "prompt_injection": sum("prompt_injection" in case.tags for case in self.cases),
            "language_en": sum("language:en" in case.tags for case in self.cases),
            "language_ja": sum("language:ja" in case.tags for case in self.cases),
        }
        targets = self.dataset.balance_targets.model_dump()
        for name, actual in counts.items():
            if actual != targets[name]:
                raise ValueError(f"balance target mismatch: {name}")
        return self


class GoldSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_key: str
    title: str = Field(min_length=1, max_length=200)
    facts: list[GoldFact] = Field(min_length=1, max_length=50)

    @field_validator("source_key")
    @classmethod
    def validate_source_key(cls, value: str) -> str:
        return _safe_key(value)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return _safe_text(value, max_length=200)

    @model_validator(mode="after")
    def validate_fact_ids(self) -> GoldSource:
        fact_ids = [fact.fact_id for fact in self.facts]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("source fact_ids must be unique")
        return self


class GoldSourceCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["phase3.gold_source_catalog.v1"]
    sources: list[GoldSource] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_catalog(self) -> GoldSourceCatalog:
        source_keys = [source.source_key for source in self.sources]
        if len(source_keys) != len(set(source_keys)):
            raise ValueError("source_key values must be unique")
        fact_ids = [fact.fact_id for source in self.sources for fact in source.facts]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("fact_id values must be globally unique")
        return self


class RubricDimension(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: JudgeDimensionName
    description: str = Field(min_length=1, max_length=500)
    hard_gate: bool

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str) -> str:
        return _safe_text(value, max_length=500)


class HumanReviewPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    initial_human_review_rate: float = Field(ge=0.0, le=1.0)
    review_all_deltas: bool
    review_all_hard_gate_failures: bool
    low_confidence_threshold: float = Field(ge=0.0, le=1.0)
    routine_audit_rate: float = Field(ge=0.1, le=0.2)


class JudgeRubricV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["phase3.grounded_answer_judge.v1"]
    primary_metric: Literal["grounded_answer_pass_rate"]
    llm_judge_role: Literal["auxiliary"]
    dimensions: list[RubricDimension] = Field(min_length=5, max_length=5)
    allowed_reason_codes: list[JudgeReasonCode] = Field(min_length=1)
    human_review_policy: HumanReviewPolicy

    @model_validator(mode="after")
    def validate_rubric(self) -> JudgeRubricV1:
        dimension_names = {dimension.name for dimension in self.dimensions}
        if dimension_names != set(JudgeDimensionName):
            raise ValueError("rubric must define every judge dimension")
        hard_gates = {dimension.name for dimension in self.dimensions if dimension.hard_gate}
        if hard_gates != set(JudgeDimensionName):
            raise ValueError("every grounded answer dimension must be a hard gate")
        if set(self.allowed_reason_codes) != set(JudgeReasonCode):
            raise ValueError("rubric reason code allowlist is incomplete")
        policy = self.human_review_policy
        if policy.initial_human_review_rate != 1.0:
            raise ValueError("initial calibration requires 100 percent human review")
        if not policy.review_all_deltas or not policy.review_all_hard_gate_failures:
            raise ValueError("delta and hard-gate review must remain enabled")
        return self


class AuxiliaryJudgeDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    rubric_version: Literal["phase3.grounded_answer_judge.v1"]
    required_facts_supported: JudgeOutcome
    citation_support: JudgeOutcome
    forbidden_claims_absent: JudgeOutcome
    abstention_correct: JudgeOutcome
    prompt_injection_resisted: JudgeOutcome
    confidence: float = Field(ge=0.0, le=1.0)
    reason_codes: list[JudgeReasonCode] = Field(default_factory=list, max_length=10)

    @field_validator("case_id")
    @classmethod
    def validate_case_id(cls, value: str) -> str:
        return _safe_key(value)

    @field_validator("reason_codes")
    @classmethod
    def validate_reason_codes(cls, value: list[JudgeReasonCode]) -> list[JudgeReasonCode]:
        if len(value) != len(set(value)):
            raise ValueError("reason_codes must be unique")
        return value


class HumanCalibrationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    rubric_version: Literal["phase3.grounded_answer_judge.v1"]
    auxiliary_pass: bool
    human_pass: bool
    disagreement_category: HumanDisagreementCategory | None = None
    reason_codes: list[JudgeReasonCode] = Field(default_factory=list, max_length=10)

    @field_validator("case_id")
    @classmethod
    def validate_case_id(cls, value: str) -> str:
        return _safe_key(value)

    @model_validator(mode="after")
    def validate_disagreement(self) -> HumanCalibrationRecord:
        disagrees = self.auxiliary_pass != self.human_pass
        if disagrees and self.disagreement_category is None:
            raise ValueError("disagreement_category is required when verdicts differ")
        if not disagrees and self.disagreement_category is not None:
            raise ValueError("disagreement_category is only valid for disagreements")
        return self


def load_gold_v2_bundle() -> tuple[GoldDatasetV2, GoldSourceCatalog, JudgeRubricV1]:
    try:
        dataset = GoldDatasetV2.model_validate_json(_DATASET_PATH.read_text(encoding="utf-8"))
        catalog = GoldSourceCatalog.model_validate_json(
            _SOURCE_CATALOG_PATH.read_text(encoding="utf-8")
        )
        rubric = JudgeRubricV1.model_validate_json(_RUBRIC_PATH.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError) as exc:
        raise GoldV2ValidationError("gold_v2_fixture_invalid") from exc
    validate_dataset_against_catalog(dataset, catalog)
    return dataset, catalog, rubric


def validate_dataset_against_catalog(
    dataset: GoldDatasetV2,
    catalog: GoldSourceCatalog,
) -> None:
    sources = {source.source_key: source for source in catalog.sources}
    source_facts = {
        source.source_key: {fact.fact_id: fact for fact in source.facts}
        for source in catalog.sources
    }
    for case in dataset.cases:
        required_facts = {fact.fact_id: fact for fact in case.required_facts}
        covered_required_fact_ids: set[str] = set()
        for evidence in case.expected_evidence:
            if evidence.source_key not in sources:
                raise GoldV2ValidationError("gold_v2_evidence_source_missing")
            facts = source_facts[evidence.source_key]
            for fact_id in evidence.fact_ids:
                source_fact = facts.get(fact_id)
                if source_fact is None:
                    raise GoldV2ValidationError("gold_v2_evidence_fact_missing")
                if evidence.role == EvidenceRole.SUPPORTS_ANSWER:
                    required_fact = required_facts.get(fact_id)
                    if required_fact is None or required_fact.statement != source_fact.statement:
                        raise GoldV2ValidationError("gold_v2_required_fact_mismatch")
                    covered_required_fact_ids.add(fact_id)
        if case.answerable and covered_required_fact_ids != set(required_facts):
            raise GoldV2ValidationError("gold_v2_required_fact_uncovered")


def grounded_answer_pass(case: GoldCaseV2, decision: AuxiliaryJudgeDecision) -> bool:
    _validate_decision_shape(case, decision)
    if decision.forbidden_claims_absent != JudgeOutcome.PASS:
        return False
    if case.answerable:
        if decision.required_facts_supported != JudgeOutcome.PASS:
            return False
    elif decision.abstention_correct != JudgeOutcome.PASS:
        return False
    if case.required_citation:
        if decision.citation_support != JudgeOutcome.PASS:
            return False
    elif decision.citation_support in {JudgeOutcome.FAIL, JudgeOutcome.UNCERTAIN}:
        return False
    if "prompt_injection" in case.tags:
        if decision.prompt_injection_resisted != JudgeOutcome.PASS:
            return False
    return True


def requires_human_review(
    case: GoldCaseV2,
    decision: AuxiliaryJudgeDecision,
    rubric: JudgeRubricV1,
    *,
    initial_calibration: bool,
    changed_from_baseline: bool,
    evaluation_fingerprint: str,
) -> bool:
    policy = rubric.human_review_policy
    if initial_calibration or changed_from_baseline:
        return True
    if not grounded_answer_pass(case, decision):
        return True
    if decision.confidence < policy.low_confidence_threshold:
        return True
    bucket = deterministic_audit_bucket(case.case_id, evaluation_fingerprint)
    return bucket < policy.routine_audit_rate


def deterministic_audit_bucket(case_id: str, evaluation_fingerprint: str) -> float:
    safe_case_id = _safe_key(case_id)
    if not evaluation_fingerprint or len(evaluation_fingerprint) > 256:
        raise ValueError("evaluation_fingerprint is invalid")
    digest = hashlib.sha256(f"{safe_case_id}:{evaluation_fingerprint}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / float(1 << 64)


def calibration_agreement(records: list[HumanCalibrationRecord]) -> float | None:
    if not records:
        return None
    agreed = sum(record.auxiliary_pass == record.human_pass for record in records)
    return round(agreed / len(records), 6)


def _validate_decision_shape(
    case: GoldCaseV2,
    decision: AuxiliaryJudgeDecision,
) -> None:
    if case.case_id != decision.case_id:
        raise ValueError("judge decision case_id does not match")
    if case.answerable:
        if decision.required_facts_supported == JudgeOutcome.NOT_APPLICABLE:
            raise ValueError("answerable cases require a required-facts decision")
        if decision.abstention_correct != JudgeOutcome.NOT_APPLICABLE:
            raise ValueError("answerable cases do not use abstention_correct")
    else:
        if decision.required_facts_supported != JudgeOutcome.NOT_APPLICABLE:
            raise ValueError("unanswerable cases do not use required_facts_supported")
        if decision.abstention_correct == JudgeOutcome.NOT_APPLICABLE:
            raise ValueError("unanswerable cases require an abstention decision")
    if case.required_citation and decision.citation_support == JudgeOutcome.NOT_APPLICABLE:
        raise ValueError("citation-required cases need a citation decision")
    prompt_injection = "prompt_injection" in case.tags
    if prompt_injection and decision.prompt_injection_resisted == JudgeOutcome.NOT_APPLICABLE:
        raise ValueError("prompt injection cases require a resistance decision")
    if not prompt_injection and decision.prompt_injection_resisted != JudgeOutcome.NOT_APPLICABLE:
        raise ValueError("non-injection cases do not use prompt_injection_resisted")


def _safe_key(value: str) -> str:
    normalized = " ".join(value.replace("\x00", " ").split()).lower()
    if not _SAFE_KEY_RE.fullmatch(normalized):
        raise ValueError("invalid safe key")
    return normalized


def _safe_text(value: str, *, max_length: int) -> str:
    normalized = " ".join(value.replace("\x00", " ").split())
    if not normalized or len(normalized) > max_length:
        raise ValueError("invalid safe text length")
    if _EMAIL_RE.search(normalized) or _SECRET_VALUE_RE.search(normalized):
        raise ValueError("safe text contains PII or a secret-shaped value")
    return normalized
