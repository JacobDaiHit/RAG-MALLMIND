"""debug_exact.py - Replicate exact API call parameters."""
import sys, json, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from rag.recommendation.recommendation_pipeline import recommend_shopping_products

query = sys.argv[1] if len(sys.argv) > 1 else "500元以下的零食"
print(f"Query: {query}")

# Replicate API params: use_llm=True, skip_keyword_check=True, use_milvus_retrieval=True
result = recommend_shopping_products(
    query,
    use_llm=True,
    skip_keyword_check=True,
    use_milvus_retrieval=True,
    catalog_scope="ecommerce",
)

req = result.requirement
print(f"\nraw_query: {req.raw_query}")
print(f"desired_categories: {[c.value for c in (req.desired_categories or [])]}")
print(f"price_max: {req.price_max}")
print(f"must_have_terms: {req.must_have_terms}")
print(f"excluded_terms: {req.excluded_terms}")

print(f"\nCards: {len(result.product_cards)}")
for card in result.product_cards[:5]:
    pid = card.get("product_id", "?")
    title = card.get("title", "?")[:50]
    price = card.get("price", "?")
    print(f"  [{pid}] {title} | {price}")

trace = result.trace
print(f"\nno_match_reason: {trace.get('no_match_reason', '')}")
print(f"clarification_required: {trace.get('clarification_required', False)}")
print(f"inferred_product_type: {trace.get('inferred_product_type', '')}")
print(f"requested_missing_subcategory: {trace.get('no_match_reason', '')}")

sf = trace.get("structured_filter", {})
if sf:
    for cat, diag in sf.items():
        print(f"\n  Filter [{cat}]:")
        for k in ("raw_count", "after_stock_count", "after_exclusion_count",
                   "after_target_count", "after_must_have_count", "after_budget_count",
                   "returned_count", "inferred_product_type",
                   "product_type_filter_applied", "product_type_candidate_count",
                   "budget_gap_reason"):
            print(f"    {k}: {diag.get(k, '?')}")
