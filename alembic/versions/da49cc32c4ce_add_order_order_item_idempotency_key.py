"""add order, order_item, idempotency_key

Creates the checkout tables:

* ``orders`` — immutable order header with the ``order_status`` enum and CHECK
  constraints enforcing invariant 7 (``discount_cents`` in
  ``[0, gross_total_cents]``, ``net_total_cents = gross_total_cents -
  discount_cents``).
* ``order_items`` — fully snapshotted lines (name + unit price copied in),
  ``line_total_cents = unit_price_cents_snapshot * quantity`` enforced.
* ``idempotency_keys`` — stored checkout responses, keyed by the client's
  ``Idempotency-Key`` (primary key => unique + indexed).

``downgrade()`` drops all three tables and the ``order_status`` enum type.

Revision ID: da49cc32c4ce
Revises: 8f1e4f2374ca
Create Date: 2026-09-03 20:31:26.273049

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "da49cc32c4ce"
down_revision: Union[str, None] = "8f1e4f2374ca"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

order_status = sa.Enum("success", name="order_status")


def upgrade() -> None:
    op.create_table(
        "idempotency_keys",
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("cart_id", sa.Uuid(), nullable=False),
        sa.Column(
            "response_body",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "orders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("cart_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            order_status,
            server_default="success",
            nullable=False,
        ),
        sa.Column("gross_total_cents", sa.Integer(), nullable=False),
        sa.Column(
            "discount_cents",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("net_total_cents", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "gross_total_cents >= 0",
            name="ck_orders_gross_total_non_negative",
        ),
        sa.CheckConstraint(
            "discount_cents >= 0", name="ck_orders_discount_non_negative"
        ),
        sa.CheckConstraint(
            "discount_cents <= gross_total_cents",
            name="ck_orders_discount_not_over_gross",
        ),
        sa.CheckConstraint(
            "net_total_cents = gross_total_cents - discount_cents",
            name="ck_orders_net_total_consistent",
        ),
        sa.ForeignKeyConstraint(["cart_id"], ["carts.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cart_id"),
    )
    op.create_table(
        "order_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("product_name_snapshot", sa.String(length=255), nullable=False),
        sa.Column("unit_price_cents_snapshot", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("line_total_cents", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "quantity > 0", name="ck_order_items_quantity_positive"
        ),
        sa.CheckConstraint(
            "unit_price_cents_snapshot >= 0",
            name="ck_order_items_unit_price_non_negative",
        ),
        sa.CheckConstraint(
            "line_total_cents = unit_price_cents_snapshot * quantity",
            name="ck_order_items_line_total_consistent",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["product_id"], ["products.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "order_id", "product_id", name="uq_order_items_order_id_product_id"
        ),
    )


def downgrade() -> None:
    op.drop_table("order_items")
    op.drop_table("orders")
    op.drop_table("idempotency_keys")
    order_status.drop(op.get_bind(), checkfirst=True)
