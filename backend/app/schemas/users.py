from __future__ import annotations

from pydantic import BaseModel, EmailStr


class UserPublic(BaseModel):
    user_id: int
    email: EmailStr
    display_name: str
    role: str
