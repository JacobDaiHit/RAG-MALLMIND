"""Public Pydantic catalog and recommendation data contracts.

The active application imports product/SKU/category models from
``recommendation.py`` through this module. The obsolete SQLAlchemy chat and
parent-chunk models were removed with their unused storage stack.
"""

from rag.schemas.recommendation import (
    ApiProduct,
    ComponentCategory,
    ProductFAQ,
    ProductReview,
    ProductSku,
)

__all__ = [
    "ApiProduct",
    "ComponentCategory",
    "ProductFAQ",
    "ProductReview",
    "ProductSku",
]
