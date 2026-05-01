from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, object] = Field(default_factory=dict)
    request_id: str | None = None


class PaginationMeta(BaseModel):
    page: int
    page_size: int
    total: int | None = None


class Meta(BaseModel):
    request_id: str | None = None
    pagination: PaginationMeta | None = None


class Envelope(BaseModel, Generic[T]):
    data: T
    meta: Meta = Field(default_factory=Meta)


class ListEnvelope(BaseModel, Generic[T]):
    data: list[T]
    meta: Meta
