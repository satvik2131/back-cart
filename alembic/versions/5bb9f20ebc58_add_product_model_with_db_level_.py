"""add product model with db-level constraints

Creates the ``products`` table. Non-negativity of ``unit_price_cents`` and
``inventory`` is enforced by named CHECK constraints at the database level.

Revision ID: 5bb9f20ebc58
Revises:
Create Date: 2026-09-03 19:25:22.582244

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5bb9f20ebc58"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "products",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("unit_price_cents", sa.Integer(), nullable=False),
        sa.Column("inventory", sa.Integer(), nullable=False),
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
            "inventory >= 0", name="ck_products_inventory_non_negative"
        ),
        sa.CheckConstraint(
            "unit_price_cents >= 0",
            name="ck_products_unit_price_cents_non_negative",
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("products")
