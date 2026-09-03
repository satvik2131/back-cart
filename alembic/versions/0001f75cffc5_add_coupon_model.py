"""add coupon model

Creates ``coupons``. ``milestone_number`` and ``code`` are UNIQUE — the
milestone constraint is the concurrency guard for generation (invariant 5). A
CHECK keeps ``status`` consistent with the ``redeemed_*`` columns.

``downgrade()`` drops the table and the ``coupon_status`` enum type.

Revision ID: 0001f75cffc5
Revises: da49cc32c4ce
Create Date: 2026-09-03 20:52:45.543670

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001f75cffc5"
down_revision: Union[str, None] = "da49cc32c4ce"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

coupon_status = sa.Enum("available", "redeemed", name="coupon_status")


def upgrade() -> None:
    op.create_table(
        "coupons",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("milestone_number", sa.Integer(), nullable=False),
        sa.Column("discount_percent", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            coupon_status,
            server_default="available",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("redeemed_by_order_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "milestone_number > 0", name="ck_coupons_milestone_positive"
        ),
        sa.CheckConstraint(
            "discount_percent >= 1 AND discount_percent <= 100",
            name="ck_coupons_discount_percent_range",
        ),
        sa.CheckConstraint(
            "(status = 'available' "
            "AND redeemed_at IS NULL AND redeemed_by_order_id IS NULL) "
            "OR (status = 'redeemed' "
            "AND redeemed_at IS NOT NULL AND redeemed_by_order_id IS NOT NULL)",
            name="ck_coupons_redeemed_consistency",
        ),
        sa.ForeignKeyConstraint(
            ["redeemed_by_order_id"], ["orders.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("milestone_number"),
        sa.UniqueConstraint("code"),
    )


def downgrade() -> None:
    op.drop_table("coupons")
    coupon_status.drop(op.get_bind(), checkfirst=True)
