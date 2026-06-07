import json
from pathlib import Path

products = json.loads(Path('data/ecommerce_products/products.json').read_text(encoding='utf-8'))
print(f"Total products: {len(products)}")

cats = {}
subs = {}
for p in products:
    c = p.get("category_key", "?")
    s = p.get("sub_category", "?")
    cats[c] = cats.get(c, 0) + 1
    subs[s] = subs.get(s, 0) + 1

print("\nCategory distribution:")
for k, v in sorted(cats.items()):
    print(f"  {k}: {v}")

print(f"\nSub-category count: {len(subs)}")
for k, v in sorted(subs.items(), key=lambda x: -x[1])[:20]:
    print(f"  {k}: {v}")

# Check new products
new_prods = [p for p in products if int(p["product_id"].split("_")[-1]) > 25]
print(f"\nNew products (ID > 025): {len(new_prods)}")
for p in new_prods[:5]:
    print(f"  {p['product_id']} cat={p['category_key']} sub={p['sub_category']} price={p['base_price']} brand={p['brand']}")

# Check for any missing fields
required_fields = ["product_id", "title", "brand", "category", "category_key", "sub_category",
                   "base_price", "min_price", "max_price", "currency", "stock_status", "skus",
                   "description", "faqs", "reviews", "review_count", "rating_avg", "tags",
                   "best_for", "not_good_for", "supported_scenarios", "metadata"]
missing = []
for p in products:
    for f in required_fields:
        if f not in p:
            missing.append((p["product_id"], f))
if missing:
    print(f"\nMissing fields: {len(missing)}")
    for pid, f in missing[:10]:
        print(f"  {pid}: {f}")
else:
    print("\nAll required fields present in all products.")
