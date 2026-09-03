import uuid

from pydantic import BaseModel


class ProductSales(BaseModel):
    product_id: uuid.UUID
    product_name: str
    quantity_sold: int


class AdminReport(BaseModel):
    """Live aggregate over orders and coupons. All money in integer cents.

    ``net_revenue_cents`` is ``gross_revenue_cents - total_discount_cents``; a
    test asserts it also equals the sum of per-order ``net_total_cents`` (which
    the per-order CHECK constraint guarantees).
    """

    total_successful_orders: int
    gross_revenue_cents: int
    total_discount_cents: int
    net_revenue_cents: int
    coupons_generated: int
    coupons_available: int
    coupons_redeemed: int
    quantity_sold_by_product: list[ProductSales]
