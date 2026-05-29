from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

MARKER_RE = re.compile(r"\[(\d+)\]")
MAX_MARKER_DIGITS = 6


class CitationBuildError(RuntimeError):
    pass


@dataclass(frozen=True)
class CitationSource:
    local_citation_id: int
    retrieval_run_item_id: int
    document_chunk_id: int
    source_label: str
    snippet: str
    page_from: int | None
    page_to: int | None
    section_title: str | None
    source_type: str = "upload"
    source_url: str | None = None


@dataclass(frozen=True)
class CitationMarker:
    local_citation_id: int
    start: int
    end: int


@dataclass(frozen=True)
class ParsedGenerationOutput:
    answer_text: str
    markers: list[CitationMarker]
    unique_marker_ids: list[int]


def parse_generation_output(content: str) -> ParsedGenerationOutput:
    answer_text = _normalize_answer_text(content)
    markers: list[CitationMarker] = []
    for match in MARKER_RE.finditer(answer_text):
        raw_marker = match.group(1)
        if len(raw_marker) > MAX_MARKER_DIGITS:
            raise CitationBuildError("citation_build_failed")
        markers.append(
            CitationMarker(
                local_citation_id=int(raw_marker),
                start=match.start(),
                end=match.end(),
            )
        )
    unique_marker_ids = _unique_in_order(marker.local_citation_id for marker in markers)
    return ParsedGenerationOutput(
        answer_text=answer_text,
        markers=markers,
        unique_marker_ids=unique_marker_ids,
    )


def validate_generation_citations(
    parsed: ParsedGenerationOutput,
    *,
    source_map: list[CitationSource],
) -> list[CitationSource]:
    if not parsed.markers:
        raise CitationBuildError("citation_build_failed")
    if not MARKER_RE.sub("", parsed.answer_text).strip():
        raise CitationBuildError("citation_build_failed")

    if any(local_id < 1 for local_id in parsed.unique_marker_ids):
        raise CitationBuildError("citation_build_failed")

    source_by_local_id = {}
    for source in source_map:
        if source.local_citation_id < 1:
            raise CitationBuildError("citation_build_failed")
        if source.local_citation_id in source_by_local_id:
            raise CitationBuildError("citation_build_failed")
        source_by_local_id[source.local_citation_id] = source
    if not source_by_local_id:
        raise CitationBuildError("citation_build_failed")

    unknown = [
        local_id for local_id in parsed.unique_marker_ids if local_id not in source_by_local_id
    ]
    if unknown:
        raise CitationBuildError("citation_build_failed")

    return [source_by_local_id[local_id] for local_id in parsed.unique_marker_ids]


def _unique_in_order(values: Iterable[int]) -> list[int]:
    seen: set[int] = set()
    unique: list[int] = []
    for value in values:
        if not isinstance(value, int):
            continue
        if value not in seen:
            seen.add(value)
            unique.append(value)
    return unique


def _normalize_answer_text(content: str) -> str:
    lines = [" ".join(line.split()) for line in content.replace("\x00", " ").splitlines()]
    return "\n".join(line for line in lines if line)
