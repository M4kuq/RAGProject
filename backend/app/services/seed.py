from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db.models import Role, SystemSetting, User, UserSetting


def seed(db: Session) -> None:
    roles: dict[str, Role] = {}
    for name in ("admin", "viewer"):
        role = db.scalar(select(Role).where(Role.role_name == name))
        if not role:
            role = Role(role_name=name, description=f"Phase1 {name} role")
            db.add(role)
            db.flush()
        roles[name] = role

    users = [
        ("admin@example.com", "Admin", "admin"),
        ("viewer@example.com", "Viewer", "viewer"),
    ]
    for email, display_name, role_name in users:
        user = db.scalar(select(User).where(User.email == email))
        if not user:
            user = User(
                role_id=roles[role_name].role_id,
                email=email,
                display_name=display_name,
                password_hash=hash_password("password"),
                status="active",
            )
            db.add(user)
            db.flush()
            db.add(UserSetting(user_id=user.user_id))

    defaults = {
        "rag.fake_mode": {"enabled": True},
        "rag.allowed_file_extensions": {"items": [".pdf", ".docx", ".txt", ".md", ".csv"]},
    }
    for key, value in defaults.items():
        if not db.get(SystemSetting, key):
            db.add(SystemSetting(setting_key=key, setting_value=value))
    db.commit()
