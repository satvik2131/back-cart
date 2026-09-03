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
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class OrderStatus(str, enum.Enum):
    """Terminal state of an order.

    Only ``SUCCESS`` exists: an ``orders`` row is written only when a checkout
    transaction commits in full. A checkout that fails at any step rolls the
    whole transaction back and leaves no order, so there is no ``FAILED`` row to
    represent. See DECISIONS.md §3 "Checkout Transaction Boundary".
    """

    SUCCESS = "success"


class Order(Base):
    """An immutable record of a completed checkout (invariant 3).

    Every money field is an integer count of minor units. The DB enforces
    invariant 7: ``discount_cents`` in ``[0, gross_total_cents]`` and
    ``net_total_cents = gross_total_cents - discount_cents`` (so ``net`` is
    never negative), via CHECK constraints.
    """

    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint(
            "gross_total_cents >= 0", name="ck_orders_gross_total_non_negative"
        ),
        CheckConstraint(
            "discount_cents >= 0", name="ck_orders_discount_non_negative"
        ),
        CheckConstraint(
            "discount_cents <= gross_total_cents",
            name="ck_orders_discount_not_over_gross",
        ),
        CheckConstraint(
            "net_total_cents = gross_total_cents - discount_cents",
            name="ck_orders_net_total_consistent",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), primary_key=True, default=uuid.uuid4
    )
    # UNIQUE + ON DELETE RESTRICT: a cart yields at most one order (a structural
    # backstop to the cart-status check), and a cart with an order cannot vanish.
    cart_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("carts.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    status: Mapped[OrderStatus] = mapped_column(
        Enum(
            OrderStatus,
            name="order_status",
            native_enum=True,
            values_callable=lambda enum_cls: [m.value for m in enum_cls],
        ),
        nullable=False,
        default=OrderStatus.SUCCESS,
        server_default=OrderStatus.SUCCESS.value,
    )
    gross_total_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    discount_cents: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    net_total_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class OrderItem(Base):
    """One line of an order, fully snapshotted at checkout time.

    ``product_name_snapshot`` and ``unit_price_cents_snapshot`` are copied from
    the product when the order is created and never read back through
    ``product_id`` afterwards — the order stays correct if the product's name or
    price later changes (invariant 3). ``product_id`` is retained only as a
    grouping key for reporting, with ON DELETE RESTRICT so the join stays valid.
    """

    __tablename__ = "order_items"
    __table_args__ = (
        UniqueConstraint(
            "order_id", "product_id", name="uq_order_items_order_id_product_id"
        ),
        CheckConstraint(
            "quantity > 0", name="ck_order_items_quantity_positive"
        ),
        CheckConstraint(
            "unit_price_cents_snapshot >= 0",
            name="ck_order_items_unit_price_non_negative",
        ),
        CheckConstraint(
            "line_total_cents = unit_price_cents_snapshot * quantity",
            name="ck_order_items_line_total_consistent",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), primary_key=True, default=uuid.uuid4
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    product_name_snapshot: Mapped[str] = mapped_column(
        String(255), nullable=False
    )
    unit_price_cents_snapshot: Mapped[int] = mapped_column(
        Integer, nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    line_total_cents: Mapped[int] = mapped_column(Integer, nullable=False)

    order: Mapped["Order"] = relationship(back_populates="items")


class IdempotencyKey(Base):
    """A stored checkout response, keyed by the client's ``Idempotency-Key``.

    The presence of a row is the record that a checkout for ``key`` already
    happened; the key column is the primary key, so it is unique and indexed and
    a concurrent second insert of the same key fails at the DB. ``cart_id`` is
    informational (which cart the key was used for) — deliberately not a foreign
    key, so replay records are independent of cart lifecycle.
    """

    __tablename__ = "idempotency_keys"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    cart_id: Mapped[uuid.UUID] = mapped_column(Uuid(), nullable=False)
    response_body: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
