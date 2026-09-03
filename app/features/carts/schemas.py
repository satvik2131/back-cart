import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.features.carts.models import CartStatus


class CartItemCreate(BaseModel):
    """Body for ``POST /carts/{id}/items``."""

    product_id: uuid.UUID
    quantity: int = Field(gt=0, description="Units to add; must be >= 1.")


class CartItemUpdate(BaseModel):
    """Body for ``PATCH /carts/{id}/items/{item_id}``.

    ``quantity == 0`` removes the item; negative is rejected (422).
    """

    quantity: int = Field(ge=0, description="New absolute quantity; 0 removes.")


class CartItemRead(BaseModel):
    """One line of a cart, with price fields resolved from the product now."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID
    product_name: str
    unit_price_cents: int
    quantity: int
    line_subtotal_cents: int


class CartRead(BaseModel):
    """A cart with its lines and a computed total.

    All money is integer minor units. ``total_cents`` and each
    ``line_subtotal_cents`` are computed at read time from current product
    prices — nothing price-related is stored on the cart.
    """

    id: uuid.UUID
    status: CartStatus
    items: list[CartItemRead]
    total_cents: int
    created_at: datetime
    updated_at: datetime
