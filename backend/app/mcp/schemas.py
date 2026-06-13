from __future__ import annotations

from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator

McpRagSearchStrategy = Literal["dense", "sparse", "hybrid", "agentic_router"]
McpCompareStrategy = Literal[
    "dense",
    "sparse",
    "hybrid",
    "agentic_router",
    "llm_tool_orchestrator",
    "langchain_agentic",
    "langgraph_agentic",
]


def _default_compare_strategies() -> list[McpCompareStrategy]:
    return ["dense", "sparse", "hybrid", "agentic_router"]


class McpInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class McpRagSearchInput(McpInputModel):
    query: str = Field(min_length=1, max_length=8000)
    strategy: McpRagSearchStrategy = "dense"
    top_k: int | None = Field(default=None, ge=1, le=20)
    rerank_top_n: int | None = Field(default=None, ge=1, le=20)
    include_trace_summary: bool | None = None

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("query must not be blank")
        return stripped


class McpRagAskInput(McpInputModel):
    question: str = Field(min_length=1, max_length=8000)
    strategy: Literal[
        "dense",
        "hybrid",
        "agentic_router",
        "llm_tool_orchestrator",
        "langchain_agentic",
        "langgraph_agentic",
    ] = "dense"
    top_k: int | None = Field(default=None, ge=1, le=20)
    rerank_top_n: int | None = Field(default=None, ge=1, le=20)
    include_citations: bool = True
    include_confidence: bool = True
    include_trace_summary: bool | None = None

    @field_validator("question")
    @classmethod
    def normalize_question(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("question must not be blank")
        return stripped


class McpListDocumentsInput(McpInputModel):
    status: Literal["active", "archived"] | None = "active"
    display_status: (
        Literal["active", "pending_review", "processing", "failed", "archived"] | None
    ) = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class McpGetDocumentStatusInput(McpInputModel):
    logical_document_id: int = Field(ge=1)


class McpGetJobStatusInput(McpInputModel):
    job_id: int = Field(ge=1)


class McpListEvaluationRunsInput(McpInputModel):
    status: Literal["queued", "running", "succeeded", "failed", "canceled"] | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class McpGetEvaluationResultInput(McpInputModel):
    evaluation_run_id: int = Field(ge=1)


class McpGetRetrievalTraceInput(McpInputModel):
    retrieval_run_id: int = Field(ge=1)


class McpCompareStrategiesInput(McpInputModel):
    evaluation_dataset_id: int | None = Field(default=None, ge=1)
    strategies: list[McpCompareStrategy] = Field(
        default_factory=_default_compare_strategies,
        min_length=1,
        # Keep in lockstep with the McpCompareStrategy enum so the Pydantic limit
        # never rejects a strategy list the MCP tool schema itself advertises.
        max_length=len(get_args(McpCompareStrategy)),
    )
    mode: Literal["latest_results"] = "latest_results"

    @field_validator("strategies")
    @classmethod
    def normalize_strategies(
        cls,
        value: list[McpCompareStrategy],
    ) -> list[McpCompareStrategy]:
        deduped: list[McpCompareStrategy] = []
        for strategy in value:
            if strategy not in deduped:
                deduped.append(strategy)
        return deduped
