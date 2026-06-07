"""Generate additional ecommerce products to expand the catalog."""
import json
from pathlib import Path

CATALOG_PATH = Path(__file__).resolve().parent / "data" / "ecommerce_products" / "products.json"

with open(CATALOG_PATH, "r", encoding="utf-8") as f:
    existing = json.load(f)

existing_ids = {p["product_id"] for p in existing}
print(f"Existing products: {len(existing)}")
print(f"Existing IDs: {sorted(existing_ids)[:5]}...")

# Check structure of first product
sample = existing[0]
print(f"\nSample keys: {list(sample.keys())}")
print(f"Sample category: {sample.get('category')}")
print(f"Sample sub_category: {sample.get('sub_category')}")
print(f"Sample tags: {sample.get('tags', [])[:5]}")
print(f"Sample best_for: {sample.get('best_for', [])[:5]}")
print(f"Sample skus: {len(sample.get('skus', []))}")
print(f"Sample faqs: {len(sample.get('faqs', []))}")
print(f"Sample reviews: {len(sample.get('reviews', []))}")
