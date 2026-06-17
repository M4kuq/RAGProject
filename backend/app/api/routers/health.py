from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.db.session import check_database
from app.graph.neo4j_backend import neo4j_health_status

router = APIRouter()


@router.get("/health")
def health() -> dict[str, object]:
    settings = get_settings()
    return {"status": "ok", "checks": {"neo4j": neo4j_health_status(settings)}}


@router.get("/ready", response_model=None)
def ready() -> dict[str, object] | JSONResponse:
    db_ok = False
    try:
        db_ok = check_database()
    except Exception:
        db_ok = False
    payload: dict[str, object] = {
        "status": "ok" if db_ok else "degraded",
        "checks": {
            "database": db_ok,
            "neo4j": neo4j_health_status(get_settings()),
        },
    }
    if not db_ok:
        return JSONResponse(status_code=503, content=payload)
    return payload
