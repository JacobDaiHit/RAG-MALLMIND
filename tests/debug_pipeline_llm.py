"""debug_pipeline_llm.py - Run pipeline with LLM parse enabled."""
import sys, json, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from rag.recommendation.recommendation_pipeline import recommend_shopping_products

query = sys.argv[1] if len(sys.argv) > 1 else "推荐一款手机"
use_llm = "--no-llm" not in sys.argv
print(f"Query: {query}")
print(f"use_llm: {use_llm}")

result = recommend_shopping_products(query, use_llm=use_llm, catalog_scope="ecommerce")

req = result.requirement
print(f"\n--- Requirement ---")
print(f"  raw_query: {req.raw_query}")
print(f"  desired_categories: {[c.value for c in (req.desired_categories or [])]}")
print(f"  required_components: {[c.value for c in (req.required_components or [])]}")
print(f"  target_sub_categories: {req.target_sub_categories}")
print(f"  brands: {req.brands}")
print(f"  excluded_brands: {req.excluded_brands}")
print(f"  excluded_terms: {req.excluded_terms}")
print(f"  price_min: {req.price_min}, price_max: {req.price_max}")
print(f"  must_have_terms: {req.must_have_terms}")
print(f"  preferences: {req.preferences}")
print(f"  scenario: {req.scenario}")
print(f"  need_bundle: {req.need_bundle}")

print(f"\n--- Cards: {len(result.product_cards)} ---")
for card in result.product_cards[:5]:
    pid = card.get("product_id", "?")
    title = card.get("title", "?")[:50]
    price = card.get("price", "?")
    print(f"  [{pid}] {title} | {price}")

trace = result.trace
print(f"\nno_match_reason: {trace.get('no_match_reason', '')}")
print(f"clarification_required: {trace.get('clarification_required', False)}")
print(f"requested_missing_subcategory: {trace.get('requested_missing_subcategory', False)}")
print(f"inferred_product_type: {trace.get('inferred_product_type', '')}")
print(f"product_type_filter_applied: {trace.get('product_type_filter_applied', '')}")
print(f"product_type_candidate_count: {trace.get('product_type_candidate_count', '')}")

sf = trace.get("structured_filter", {})
if sf:
    print(f"\n--- Structured Filter ---")
    for cat, diag in sf.items():
        print(f"  {cat}:")
        for k in ("raw_count", "after_stock_count", "after_exclusion_count", "after_target_count",
                   "after_must_have_count", "after_budget_count", "returned_count",
                   "inferred_product_type", "product_type_filter_applied",
                   "product_type_candidate_count", "budget_gap_reason"):
            print(f"    {k}: {diag.get(k, '?')}")
