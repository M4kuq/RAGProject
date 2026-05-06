from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

ChatSessionStatus = Literal["active", "archived"]
ChatDisplayStatus = Literal["active", "archived", "temporary", "temporary_expired"]
ChatMode = ChatDisplayStatus
ChatMessageRole = Literal["user", "assistant", "system"]

MAX_TITLE_LENGTH = 255
MAX_TAG_LENGTH = 50
DEFAULT_CHAT_TITLE = "新しい会話"


def normalize_title(value: str | None) -> str:
    if value is None:
        return DEFAULT_CHAT_TITLE
    title = value.strip()
    if not title:
        raise ValueError("title must not be empty")
    if len(title) > MAX_TITLE_LENGTH:
        raise ValueError(f"title must be at most {MAX_TITLE_LENGTH} characters")
    return title


def normalize_tag_name(value: str) -> str:
    tag_name = value.strip()
    if not tag_name:
        raise ValueError("tag_name must not be empty")
    if len(tag_name) > MAX_TAG_LENGTH:
        raise ValueError(f"tag_name must be at most {MAX_TAG_LENGTH} characters")
    if "/" in tag_name or "\\" in tag_name:
        raise ValueError("tag_name must not contain path separators")
    return tag_name


class ChatTagItem(BaseModel):
    chat_session_id: int
    tag_name: str
    created_at: datetime | None = None


class ChatSessionCreateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=MAX_TITLE_LENGTH)
    temporary_flag: bool = False

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return normalize_title(value)


class ChatSessionUpdateRequest(BaseModel):
    title: str = Field(max_length=MAX_TITLE_LENGTH)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return normalize_title(value)


class ChatTagCreateRequest(BaseModel):
    tag_name: str = Field(max_length=MAX_TAG_LENGTH)

    @field_validator("tag_name")
    @classmethod
    def validate_tag_name(cls, value: str) -> str:
        return normalize_tag_name(value)


class ChatSessionItem(BaseModel):
    chat_session_id: int
    title: str
    status: ChatSessionStatus
    display_status: ChatDisplayStatus
    mode: ChatMode
    temporary_flag: bool
    ttl_expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ChatSessionDetail(ChatSessionItem):
    tags: list[ChatTagItem] = Field(default_factory=list)


class ChatMessageItem(BaseModel):
    chat_message_id: int
    chat_session_id: int
    role: ChatMessageRole
    content: str
    client_message_id: str | None = None
    linked_retrieval_run_id: int | None = None
    edited_flag: bool
    created_at: datetime
    updated_at: datetime


class ChatArchiveResponse(BaseModel):
    chat_session_id: int
    status: ChatSessionStatus
    result_code: Literal["archived", "already_archived"]


class ChatTagMutationResponse(BaseModel):
    chat_session_id: int
    tag_name: str
    result_code: Literal["created", "already_exists", "deleted", "not_found_no_op"]
