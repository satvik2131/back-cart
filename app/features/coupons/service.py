"""Coupon redemption, used from inside the checkout transaction.

Generation lives in ``app.features.admin.service`` (it is an admin action);
this module owns the ``Coupon`` model and the redeem path.

Redemption is two steps so it fits the checkout flow: :func:`quote_discount`
validates the coupon and computes the discount *before* the order row exists;
:func:`mark_redeemed` performs the atomic state transition *after*, tying the
coupon to the new order. Both run inside the caller's transaction, so if
checkout rolls back the coupon is released, never burned (invariant 6).
"""

import uuid

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import CouponAlreadyRedeemedError, InvalidCouponError
from app.features.coupons.models import Coupon, CouponStatus


async def quote_discount(
    session: AsyncSession, coupon_code: str, gross_total_cents: int
) -> tuple[Coupon, int]:
    """Validate ``coupon_code`` and return ``(coupon, discount_cents)``.

    No writes. ``discount_cents = floor(gross * percent / 100)``, clamped to
    ``gross`` so ``net`` can never go negative (``discount_percent`` is already
    CHECK-constrained to 1..100, so the clamp is belt-and-braces).
    """
    coupon = await session.scalar(
        select(Coupon).where(Coupon.code == coupon_code)
    )
    if coupon is None:
        raise InvalidCouponError(
            f"Coupon {coupon_code!r} is not a valid coupon code.",
            details={"coupon_code": coupon_code},
        )
    if coupon.status is not CouponStatus.AVAILABLE:
        raise CouponAlreadyRedeemedError(
            f"Coupon {coupon_code!r} has already been redeemed.",
            details={"coupon_code": coupon_code},
        )
    discount_cents = min(
        gross_total_cents * coupon.discount_percent // 100, gross_total_cents
    )
    return coupon, discount_cents


async def mark_redeemed(
    session: AsyncSession, coupon: Coupon, order_id: uuid.UUID
) -> None:
    """Atomically move the coupon to ``redeemed``, tied to ``order_id``.

    The ``WHERE status = 'available'`` guard + rows-affected check is the
    concurrency barrier: if a parallel checkout redeemed it first, this affects
    zero rows and aborts the caller's transaction.
    """
    result = await session.execute(
        update(Coupon)
        .where(Coupon.id == coupon.id, Coupon.status == CouponStatus.AVAILABLE)
        .values(
            status=CouponStatus.REDEEMED,
            redeemed_at=func.now(),
            redeemed_by_order_id=order_id,
        )
    )
    if result.rowcount != 1:
        raise CouponAlreadyRedeemedError(
            f"Coupon {coupon.code!r} was redeemed by another checkout.",
            details={"coupon_code": coupon.code},
        )
