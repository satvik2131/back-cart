from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.router import api_router
from app.core.config import settings
from app.core.errors import register_exception_handlers
from app.db.session import get_session
from app.features.admin.router import router as admin_router
from app.features.carts.router import router as carts_router
from app.features.orders.router import router as orders_router
from app.features.products.router import router as products_router

app = FastAPI(title=settings.APP_NAME)

register_exception_handlers(app)

app.include_router(api_router)
app.include_router(products_router)
app.include_router(carts_router)
app.include_router(orders_router)
app.include_router(admin_router)


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
