from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.core.security import normalize_email
from app.schemas.users import UserPublic


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email_input(cls, value: object) -> object:
        if isinstance(value, str):
            return normalize_email(value)
        return value


class LoginResponse(BaseModel):
    user: UserPublic


class MeResponse(UserPublic):
    pass


class CsrfResponse(BaseModel):
    csrf_token: str


class LogoutResponse(BaseModel):
    status: str
