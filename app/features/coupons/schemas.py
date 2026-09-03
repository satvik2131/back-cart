import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.features.coupons.models import CouponStatus


class CouponRead(BaseModel):
    """Response representation of a coupon."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    discount_percent: int
    milestone_number: int
    status: CouponStatus
    created_at: datetime
    redeemed_at: datetime | None
    redeemed_by_order_id: uuid.UUID | None
