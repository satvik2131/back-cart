from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ErrorEnvelope
from app.db.session import get_session
from app.features.admin import service
from app.features.admin.schemas import AdminReport
from app.features.coupons.schemas import CouponRead

# Unauthenticated per spec, but explicitly grouped and tagged as administrative.
router = APIRouter(prefix="/admin", tags=["admin"])

_ENVELOPE = {"model": ErrorEnvelope}


@router.post(
    "/coupons/generate",
    response_model=CouponRead,
    status_code=status.HTTP_201_CREATED,
    summary="[admin] Generate the coupon for the latest reached milestone",
    responses={
        409: {**_ENVELOPE, "description": "Milestone already rewarded"},
        422: {**_ENVELOPE, "description": "No milestone reached yet"},
    },
)
async def generate_coupon(
    session: AsyncSession = Depends(get_session),
) -> CouponRead:
    try:
        coupon = await service.generate_coupon(session)
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return CouponRead.model_validate(coupon)


@router.get(
    "/report",
    response_model=AdminReport,
    summary="[admin] Live revenue / coupon / sales report (read-only)",
)
async def get_report(
    session: AsyncSession = Depends(get_session),
) -> AdminReport:
    return await service.build_report(session)
