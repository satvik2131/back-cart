from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.router import api_router
from app.core.config import settings
from app.db.session import get_session

app = FastAPI(title=settings.APP_NAME)

app.include_router(api_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/db")
async def health_db(session: AsyncSession = Depends(get_session)) -> JSONResponse:
    try:
        result = await session.execute(text("SELECT 1"))
        result.scalar_one()
    except Exception as exc:  # noqa: BLE001 - surface any connectivity error
        return JSONResponse(
            status_code=503,
            content={"status": "error", "db": "unavailable", "detail": str(exc)},
        )
    return JSONResponse(content={"status": "ok", "db": "connected"})
