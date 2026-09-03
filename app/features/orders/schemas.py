import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.features.orders.models import OrderStatus


class CheckoutRequest(BaseModel):
    """Body for ``POST /carts/{id}/checkout``.

    ``coupon_code`` is accepted now but ignored until Phase 2 wires coupon
    redemption into the checkout transaction (see DECISIONS.md §7).
    """

    coupon_code: str | None = Field(
        default=None, description="Reserved for Phase 2; currently ignored."
    )


class OrderItemRead(BaseModel):
    """A snapshotted order line. Values are frozen at checkout time."""

    id: uuid.UUID
    product_id: uuid.UUID
    product_name: str
    unit_price_cents: int
    quantity: int
    line_total_cents: int


class OrderRead(BaseModel):
    id: uuid.UUID
    cart_id: uuid.UUID
    status: OrderStatus
    items: list[OrderItemRead]
    gross_total_cents: int
    discount_cents: int
    net_total_cents: int
    created_at: datetime
