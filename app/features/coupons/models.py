import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CouponStatus(str, enum.Enum):
    """A coupon is generated ``AVAILABLE`` and moves to ``REDEEMED`` at most once,
    inside the checkout transaction that consumes it (invariant 6)."""

    AVAILABLE = "available"
    REDEEMED = "redeemed"


class Coupon(Base):
    """A milestone reward.

    ``milestone_number`` is UNIQUE — that constraint (not an application check)
    is what guarantees at most one coupon per milestone even under concurrent
    admin requests (invariant 5). ``code`` is UNIQUE and is what a customer
    supplies at checkout.

    A CHECK constraint keeps ``status`` and the ``redeemed_*`` columns
    consistent: ``available`` ⇒ both null, ``redeemed`` ⇒ both set.
    """

    __tablename__ = "coupons"
    __table_args__ = (
        CheckConstraint(
            "milestone_number > 0", name="ck_coupons_milestone_positive"
        ),
        CheckConstraint(
            "discount_percent >= 1 AND discount_percent <= 100",
            name="ck_coupons_discount_percent_range",
        ),
        CheckConstraint(
            "(status = 'available' "
            "AND redeemed_at IS NULL AND redeemed_by_order_id IS NULL) "
            "OR (status = 'redeemed' "
            "AND redeemed_at IS NOT NULL AND redeemed_by_order_id IS NOT NULL)",
            name="ck_coupons_redeemed_consistency",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), primary_key=True, default=uuid.uuid4
    )
    milestone_number: Mapped[int] = mapped_column(
        Integer, nullable=False, unique=True
    )
    discount_percent: Mapped[int] = mapped_column(Integer, nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    status: Mapped[CouponStatus] = mapped_column(
        Enum(
            CouponStatus,
            name="coupon_status",
            native_enum=True,
            values_callable=lambda enum_cls: [m.value for m in enum_cls],
        ),
        nullable=False,
        default=CouponStatus.AVAILABLE,
        server_default=CouponStatus.AVAILABLE.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    redeemed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # ON DELETE RESTRICT: the order that redeemed a coupon must not vanish.
    redeemed_by_order_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("orders.id", ondelete="RESTRICT"), nullable=True
    )
