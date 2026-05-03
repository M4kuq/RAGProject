from __future__ import annotations

from collections.abc import Sequence
from typing import TypeVar

from fastapi import Request

from app.schemas.common import Meta, PaginationMeta, PaginationParams

T = TypeVar("T")


def get_request_id(request: Request) -> str | None:
    value = getattr(request.state, "request_id", None)
    return str(value) if value is not None else None


def success_response(
    data: object,
    request: Request,
    pagination: PaginationMeta | None = None,
) -> dict[str, object]:
    return {"data": data, "meta": Meta(request_id=get_request_id(request), pagination=pagination)}


def pagination_meta(params: PaginationParams, total: int) -> PaginationMeta:
    return PaginationMeta(
        page=params.page,
        page_size=params.page_size,
        total=total,
        has_next=params.offset + params.page_size < total,
    )


def paginate(items: Sequence[T], params: PaginationParams) -> tuple[list[T], PaginationMeta]:
    total = len(items)
    start = params.offset
    end = start + params.page_size
    return list(items[start:end]), pagination_meta(params, total)
