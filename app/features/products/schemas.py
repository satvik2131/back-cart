import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ProductRead(BaseModel):
    """Response representation of a product.

    Separate from the ``Product`` ORM model on purpose — endpoints map ORM
    instances into this schema and never return the ORM object directly.
    Money is an integer count of minor units (cents); it is never a float.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    unit_price_cents: int
    inventory: int
    created_at: datetime
    updated_at: datetime
