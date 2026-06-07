"""debug_pipeline.py - Run pipeline in-process with full diagnostics."""
import sys, json, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from rag.recommendation.recommendation_pipeline import recommend_shopping_products

query = sys.argv[1] if len(sys.argv) > 1 else "推荐一款手机"
print(f"Query: {query}")
print()

result = recommend_shopping_products(query, use_llm=False, catalog_scope="ecommerce")

req = result.requirement
print(f"--- Requirement ---")
print(f"  raw_query: {req.raw_query}")
print(f"  desired_categories: {[c.value for c in (req.desired_categories or [])]}")
print(f"  required_components: {[c.value for c in (req.required_components or [])]}")
print(f"  target_sub_categories: {req.target_sub_categories}")
print(f"  brands: {req.brands}")
print(f"  excluded_brands: {req.excluded_brands}")
print(f"  price_min: {req.price_min}, price_max: {req.price_max}")
print(f"  must_have_terms: {req.must_have_terms}")
print(f"  scenario: {req.scenario}")
print(f"  need_bundle: {req.need_bundle}")
print(f"  product_type (inferred): check below")
print()

# Product cards
print(f"--- Product Cards: {len(result.product_cards)} ---")
for card in result.product_cards[:5]:
    pid = card.get("product_id", "?")
    title = card.get("title", "?")[:50]
    price = card.get("price", "?")
    print(f"  [{pid}] {title} | {price}")
print()

# Comparison table
ct = result.comparison_table
if ct:
    rows = ct.get("rows", [])
    print(f"--- Comparison Table: {len(rows)} rows ---")
    for row in rows[:5]:
        print(f"  {json.dumps(row, ensure_ascii=False)[:100]}")
print()

# Trace - filter diagnostics
trace = result.trace
sf = trace.get("structured_filter", {})
if sf:
    print(f"--- Structured Filter Diagnostics ---")
    for cat, diag in sf.items():
        print(f"  Category: {cat}")
        for k, v in diag.items():
            print(f"    {k}: {v}")
    print()

# no_match_reason
nmr = trace.get("no_match_reason", "")
print(f"no_match_reason: {nmr}")
print(f"budget_gap_reason: {trace.get('budget_gap_reason', '')}")
print()

# Full trace keys
print(f"--- Trace Keys ---")
for k in sorted(trace.keys()):
    v = trace[k]
    if isinstance(v, (str, int, float, bool)):
        print(f"  {k}: {v}")
    elif isinstance(v, dict) and len(v) < 5:
        print(f"  {k}: {json.dumps(v, ensure_ascii=False)[:100]}")
    else:
        print(f"  {k}: ({type(v).__name__}, len={len(str(v)[:5])})")
