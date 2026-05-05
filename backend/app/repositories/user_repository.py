from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Role, User


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == email))


def get_user_by_id(db: Session, user_id: int) -> User | None:
    return db.get(User, user_id)


def get_role_name(db: Session, user: User) -> str | None:
    return db.scalar(select(Role.role_name).where(Role.role_id == user.role_id))
