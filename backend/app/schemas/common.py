from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ErrorBody(BaseModel):
    code: str
    message: str
    details: object = Field(default_factory=dict)


class PaginationMeta(BaseModel):
    page: int
    page_size: int
    total: int | None = None
    has_next: bool = False


class Meta(BaseModel):
    request_id: str | None = None
    pagination: PaginationMeta | None = None


class ErrorEnvelope(BaseModel):
    error: ErrorBody
    meta: Meta


class Envelope(BaseModel, Generic[T]):
    data: T
    meta: Meta = Field(default_factory=Meta)


class ListEnvelope(BaseModel, Generic[T]):
    data: list[T]
    meta: Meta


class PaginationParams(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size
