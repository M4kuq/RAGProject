from __future__ import annotations

from collections.abc import Iterable

from app.core.errors import PermissionDenied

ADMIN_ROLE = "admin"
VIEWER_ROLE = "viewer"
KNOWN_ROLES = {ADMIN_ROLE, VIEWER_ROLE}


def ensure_known_role(role_name: str | None) -> str:
    if role_name not in KNOWN_ROLES:
        raise PermissionDenied()
    return role_name


def ensure_role(role_name: str | None, allowed_roles: Iterable[str]) -> str:
    actual = ensure_known_role(role_name)
    if actual not in set(allowed_roles):
        raise PermissionDenied()
    return actual


def ensure_admin(role_name: str | None) -> str:
    return ensure_role(role_name, {ADMIN_ROLE})


def ensure_viewer_or_admin(role_name: str | None) -> str:
    return ensure_role(role_name, {ADMIN_ROLE, VIEWER_ROLE})
