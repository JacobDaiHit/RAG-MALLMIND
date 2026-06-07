"""Remove all products with source=agent_expansion_v1 from products.json"""
import json
from pathlib import Path

f = Path("data/ecommerce_products/products.json")
products = json.loads(f.read_text(encoding="utf-8"))
print(f"Before: {len(products)} products")

original = [p for p in products if p.get("metadata", {}).get("source") != "agent_expansion_v1"]
removed = len(products) - len(original)
print(f"Removed: {removed} products (source=agent_expansion_v1)")
print(f"After: {len(original)} products")

# Verify
cats = {}
for p in original:
    c = p.get("category_key", "?")
    cats[c] = cats.get(c, 0) + 1
for k, v in sorted(cats.items()):
    print(f"  {k}: {v}")

f.write_text(json.dumps(original, ensure_ascii=False, indent=2), encoding="utf-8")
print("Done. products.json restored to original.")
