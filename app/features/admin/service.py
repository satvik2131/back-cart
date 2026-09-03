"""Administrative operations: coupon generation and the live report.

Both are unauthenticated by design (no auth is in scope) but are grouped under
the ``/admin`` prefix and the ``admin`` OpenAPI tag so they are clearly marked.
"""

import secrets

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import (
    MilestoneAlreadyRewardedError,
    MilestoneNotReachedError,
)
from app.features.admin.schemas import AdminReport, ProductSales
from app.features.coupons.models import Coupon, CouponStatus
from app.features.orders.models import Order, OrderItem, OrderStatus


async def generate_coupon(session: AsyncSession) -> Coupon:
    """Generate the coupon for the latest reached milestone, if any.

    ``422 MILESTONE_NOT_REACHED`` if fewer than ``COUPON_MILESTONE_EVERY``
    successful orders exist. ``409 MILESTONE_ALREADY_REWARDED`` if that
    milestone's coupon already exists — detected by the ``milestone_number``
    UNIQUE constraint (not a check-then-insert), so it is also the guard for
    two concurrent generate calls.
    """
    every = settings.COUPON_MILESTONE_EVERY
    percent = settings.COUPON_DISCOUNT_PERCENT

    successful_orders = (
        await session.scalar(
            select(func.count())
            .select_from(Order)
            .where(Order.status == OrderStatus.SUCCESS)
        )
    ) or 0
    latest_milestone = (successful_orders // every) * every

    if latest_milestone == 0:
        raise MilestoneNotReachedError(
            f"No coupon milestone reached: {successful_orders} successful "
            f"order(s), {every} required for the first coupon.",
            details={
                "successful_orders": successful_orders,
                "next_milestone": every,
            },
        )

    coupon = Coupon(
        milestone_number=latest_milestone,
        discount_percent=percent,
        code=f"SAVE{percent}-{secrets.token_hex(5).upper()}",
        status=CouponStatus.AVAILABLE,
    )
    session.add(coupon)
    try:
        await session.flush()
    except IntegrityError as exc:
        if "milestone" not in str(exc.orig).lower():
            raise
        raise MilestoneAlreadyRewardedError(
            f"A coupon has already been generated for milestone "
            f"{latest_milestone}.",
            details={"milestone_number": latest_milestone},
        ) from exc

    await session.refresh(coupon)
    return coupon


async def build_report(session: AsyncSession) -> AdminReport:
    """Compute the report from a live query — no caching, no writes."""
    is_success = Order.status == OrderStatus.SUCCESS

    order_count, gross, discount = (
        await session.execute(
            select(
                func.count(Order.id),
                func.coalesce(func.sum(Order.gross_total_cents), 0),
                func.coalesce(func.sum(Order.discount_cents), 0),
            ).where(is_success)
        )
    ).one()

    coupon_counts: dict[CouponStatus, int] = dict(
        (
            await session.execute(
                select(Coupon.status, func.count(Coupon.id)).group_by(
                    Coupon.status
                )
            )
        ).all()
    )

    per_product = (
        await session.execute(
            select(
                OrderItem.product_id,
                OrderItem.product_name_snapshot,
                func.sum(OrderItem.quantity),
            )
            .join(Order, Order.id == OrderItem.order_id)
            .where(is_success)
            .group_by(OrderItem.product_id, OrderItem.product_name_snapshot)
            .order_by(OrderItem.product_name_snapshot)
        )
    ).all()

    return AdminReport(
        total_successful_orders=order_count,
        gross_revenue_cents=gross,
        total_discount_cents=discount,
        net_revenue_cents=gross - discount,
        coupons_generated=sum(coupon_counts.values()),
        coupons_available=coupon_counts.get(CouponStatus.AVAILABLE, 0),
        coupons_redeemed=coupon_counts.get(CouponStatus.REDEEMED, 0),
        quantity_sold_by_product=[
            ProductSales(
                product_id=product_id,
                product_name=name,
                quantity_sold=quantity,
            )
            for product_id, name, quantity in per_product
        ],
    )
