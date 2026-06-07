"""Check product structure."""
import json
with open('data/ecommerce_products/products.json', encoding='utf-8') as f:
    products = json.load(f)

# Show full structure of first product
p = products[0]
print(f"First product keys: {list(p.keys())}")
print(json.dumps(p, ensure_ascii=False, indent=2))

# Show a beauty product
beauty = [p for p in products if 'beauty' in str(p.get('product_id', '')) or 'beauty' in str(p.get('category', ''))]
if beauty:
    print(f"\nBeauty product:")
    print(json.dumps(beauty[0], ensure_ascii=False, indent=2))

# Check what fields contain price info
for p in products[:5]:
    pid = p.get("product_id", "?")
    for key in ["price", "base_price", "min_price", "amount", "cost"]:
        if key in p:
            print(f"  {pid}.{key} = {p[key]}")
