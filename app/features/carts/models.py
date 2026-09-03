import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class CartStatus(str, enum.Enum):
    """Lifecycle of a cart. A cart moves ``OPEN -> CHECKED_OUT`` exactly once
    (invariant 2); there is no path back."""

    OPEN = "open"
    CHECKED_OUT = "checked_out"


class Cart(Base):
    """A mutable collection of items until it is checked out.

    ``status`` is a native Postgres enum (``cart_status``), not a free string,
    so no write can put a cart into an unknown state.
    """

    __tablename__ = "carts"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), primary_key=True, default=uuid.uuid4
    )
    status: Mapped[CartStatus] = mapped_column(
        Enum(
            CartStatus,
            name="cart_status",
            native_enum=True,
            # Store the lowercase ``.value`` ("open"), not the member name.
            values_callable=lambda enum_cls: [m.value for m in enum_cls],
        ),
        nullable=False,
        default=CartStatus.OPEN,
        server_default=CartStatus.OPEN.value,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    items: Mapped[list["CartItem"]] = relationship(
        back_populates="cart",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class CartItem(Base):
    """One product line in a cart.

    No price is stored here — cart totals are computed live from the product's
    current price (see DECISIONS.md §3 "Cart Items Hold No Price"). The price
    snapshot happens only when an order is created at checkout.

    ``(cart_id, product_id)`` is unique: a product appears at most once per
    cart, and re-adding it increments ``quantity`` rather than inserting a
    duplicate row.
    """

    __tablename__ = "cart_items"
    __table_args__ = (
        # Also the access path for "all items in a cart" (GET /carts/{id}) and
        # the per-cart item lookup during add/update — cart_id is the leading
        # column, so no separate index on cart_id is needed.
        UniqueConstraint(
            "cart_id", "product_id", name="uq_cart_items_cart_id_product_id"
        ),
        CheckConstraint("quantity > 0", name="ck_cart_items_quantity_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), primary_key=True, default=uuid.uuid4
    )
    # ON DELETE CASCADE: the cart owns its items (parent/child).
    cart_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("carts.id", ondelete="CASCADE"), nullable=False
    )
    # ON DELETE RESTRICT: a product referenced by a cart must not disappear.
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    cart: Mapped["Cart"] = relationship(back_populates="items")
