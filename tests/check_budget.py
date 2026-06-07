"""Check earphone products and budget extraction."""
import json, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv()

# 1. Check earphone products
with open('data/ecommerce_products/products.json', encoding='utf-8') as f:
    products = json.load(f)

earphones = [p for p in products if '耳机' in p.get('title', '') or 'earphone' in str(p.get('tags', []))]
print(f"Products with '耳机' in title: {len(earphones)}")
for p in earphones:
    pid = p.get("product_id", "?")
    title = p.get("title", "?")[:50]
    bp = p.get("base_price", "?")
    print(f"  {pid}: {title} | base_price={bp}")

# 2. Check what product_type "耳机" maps to
from rag.recommendation.query_guards import PRODUCT_TYPE_CATEGORY, infer_product_type
print(f"\n耳机 maps to category: {PRODUCT_TYPE_CATEGORY.get('earphone')}")
print(f"infer_product_type('不要超过3000的耳机'): {infer_product_type('不要超过3000的耳机')}")

# 3. Check budget extraction
from rag.recommendation.tool_router import extract_budget
from rag.recommendation.recommendation_pipeline import extract_price_range

print(f"\nextract_budget('不要超过3000的耳机'): {extract_budget('不要超过3000的耳机')}")
print(f"extract_budget('有没有2000到5000的护肤品'): {extract_budget('有没有2000到5000的护肤品')}")

# Check pipeline price range extraction
try:
    result = extract_price_range('不要超过3000的耳机')
    print(f"extract_price_range('不要超过3000的耳机'): {result}")
except Exception as e:
    print(f"extract_price_range error: {e}")

try:
    result = extract_price_range('有没有2000到5000的护肤品')
    print(f"extract_price_range('有没有2000到5000的护肤品'): {result}")
except Exception as e:
    print(f"extract_price_range error: {e}")
