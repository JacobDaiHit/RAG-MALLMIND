"""Check beauty products and prices."""
import json
with open('data/ecommerce_products/products.json', encoding='utf-8') as f:
    products = json.load(f)

beauty = [p for p in products if p.get('category') == 'beauty']
print(f"Beauty products: {len(beauty)}")
for p in beauty:
    pid = p.get("product_id", "?")
    title = p.get("title", "?")[:50]
    price = p.get("price", "?")
    print(f"  {pid}: {title} | price={price}")

# Also check earphone products
digital = [p for p in products if p.get('category') == 'digital']
print(f"\nDigital products: {len(digital)}")
for p in digital:
    pid = p.get("product_id", "?")
    title = p.get("title", "?")[:50]
    price = p.get("price", "?")
    print(f"  {pid}: {title} | price={price}")
