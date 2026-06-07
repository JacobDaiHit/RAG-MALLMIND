"""Check all categories and sample products."""
import json
from collections import Counter
with open('data/ecommerce_products/products.json', encoding='utf-8') as f:
    products = json.load(f)

cats = Counter(p.get("category", "?") for p in products)
print(f"Total products: {len(products)}")
print(f"\nCategories:")
for cat, count in cats.most_common():
    print(f"  {cat}: {count}")

# Show prices per category
for cat in sorted(cats.keys()):
    cat_prods = [p for p in products if p.get("category") == cat]
    prices = [p.get("price", 0) for p in cat_prods]
    print(f"\n{cat} ({len(cat_prods)} products): price range {min(prices):.0f} - {max(prices):.0f}")
    for p in cat_prods[:5]:
        print(f"  {p.get('product_id')}: {p.get('title', '?')[:45]} | {p.get('price')}")
