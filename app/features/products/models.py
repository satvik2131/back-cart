import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Product(Base):
    """A sellable item with a price and a finite inventory.

    ``unit_price_cents`` and ``inventory`` are non-negative integers enforced by
    DB-level CHECK constraints (see ``__table_args__``) — not just application
    validation — so no code path, migration, or manual write can leave a product
    with a negative price or negative stock.
    """

    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint(
            "unit_price_cents >= 0",
            name="ck_products_unit_price_cents_non_negative",
        ),
        CheckConstraint(
            "inventory >= 0",
            name="ck_products_inventory_non_negative",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    unit_price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    inventory: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
