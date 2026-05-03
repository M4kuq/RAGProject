from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.db.session import check_database

router = APIRouter()


@router.get("/health")
def health() -> dict[str, object]:
    return {"status": "ok", "checks": {}}


@router.get("/ready", response_model=None)
def ready() -> dict[str, object] | JSONResponse:
    db_ok = False
    try:
        db_ok = check_database()
    except Exception:
        db_ok = False
    payload: dict[str, object] = {
        "status": "ok" if db_ok else "degraded",
        "checks": {"database": db_ok},
    }
    if not db_ok:
        return JSONResponse(status_code=503, content=payload)
    return payload
