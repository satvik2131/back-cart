"""add cart and cart_item models

Creates ``carts`` (with the ``cart_status`` native enum) and ``cart_items``.
``cart_items`` has a unique ``(cart_id, product_id)`` — a product appears at most
once per cart — and a ``quantity > 0`` CHECK. No price column: cart totals are
computed live from the product (see DECISIONS.md §3).

``downgrade()`` drops both tables and the enum type it created.

Revision ID: 8f1e4f2374ca
Revises: 5bb9f20ebc58
Create Date: 2026-09-03 20:04:26.158616

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8f1e4f2374ca"
down_revision: Union[str, None] = "5bb9f20ebc58"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

cart_status = sa.Enum("open", "checked_out", name="cart_status")


def upgrade() -> None:
    op.create_table(
        "carts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            cart_status,
            server_default="open",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "cart_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("cart_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "quantity > 0", name="ck_cart_items_quantity_positive"
        ),
        sa.ForeignKeyConstraint(
            ["cart_id"], ["carts.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["product_id"], ["products.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "cart_id", "product_id", name="uq_cart_items_cart_id_product_id"
        ),
    )


def downgrade() -> None:
    op.drop_table("cart_items")
    op.drop_table("carts")
    cart_status.drop(op.get_bind(), checkfirst=True)
