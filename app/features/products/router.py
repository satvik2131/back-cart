from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ErrorEnvelope
from app.db.session import get_session
from app.features.products import service
from app.features.products.dependencies import get_product_or_404
from app.features.products.models import Product
from app.features.products.schemas import ProductRead

router = APIRouter(prefix="/products", tags=["products"])


@router.get("", response_model=list[ProductRead])
async def list_products(
    session: AsyncSession = Depends(get_session),
) -> list[ProductRead]:
    products = await service.list_products(session)
    return [ProductRead.model_validate(p) for p in products]


@router.get(
    "/{product_id}",
    response_model=ProductRead,
    responses={404: {"model": ErrorEnvelope, "description": "Product not found"}},
)
async def get_product(
    product: Product = Depends(get_product_or_404),
) -> ProductRead:
    return ProductRead.model_validate(product)
