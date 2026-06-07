"""debug_api_flow.py - Replicate the exact API flow for a query to find where cards are lost."""
import sys, json, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv()

query = sys.argv[1] if len(sys.argv) > 1 else "500元以下的零食"
print(f"Query: {query}")
print()

# Step 1: build_contextual_goal (first query in session)
from rag.recommendation.session_state import get_session, build_contextual_goal
session = get_session("debug_session")
contextual_goal = build_contextual_goal(session, query)
print(f"1. contextual_goal: {contextual_goal!r}")

# Step 2: goal_with_attachment_context (no attachments)
from rag.api.attachments import goal_with_attachment_context
contextual_goal_final = goal_with_attachment_context(contextual_goal, [])
print(f"2. contextual_goal (after attachment): {contextual_goal_final!r}")

# Step 3: validate_goal
from rag.api.app_context import validate_goal
try:
    validate_goal(contextual_goal_final, skip_keyword_check=True)
    print(f"3. validate_goal: OK")
except Exception as e:
    print(f"3. validate_goal: FAILED - {e}")

# Step 4: call recommend_shopping_products with same params as API
from rag.recommendation.recommendation_pipeline import recommend_shopping_products
from rag.recommendation.image_retrieval import retrieve_image_evidence
from rag.recommendation.product_loader import load_catalog_for_scope

# Simulate image_retrieval (no attachments)
image_evidence = retrieve_image_evidence(attachments=[], catalog=load_catalog_for_scope("ecommerce"))
print(f"4. image_evidence: status={image_evidence.status}")

# Call pipeline
print(f"5. Calling recommend_shopping_products...")
result = recommend_shopping_products(
    contextual_goal_final,
    use_llm=True,
    skip_keyword_check=True,
    use_milvus_retrieval=True,
    catalog_scope="ecommerce",
    image_retrieval_evidence=image_evidence,
)

print(f"   result.product_cards: {len(result.product_cards)}")
for card in result.product_cards[:5]:
    pid = card.get("product_id", "?")
    title = card.get("title", "?")[:50]
    price = card.get("price", "?")
    print(f"   [{pid}] {title} | {price}")

# Step 5: model_to_dict
from rag.utils.runtime_errors import sanitize_result_for_response

# Simulate what handle_recommend does
from dataclasses import asdict as model_to_dict
# Actually it's likely not dataclasses.asdict, let's check
import inspect
print()
print(f"6. model_to_dict...")

# Let's just check if result is a dataclass or has a to_dict method
print(f"   result type: {type(result)}")
print(f"   result.product_cards type: {type(result.product_cards)}")
if result.product_cards:
    print(f"   first card type: {type(result.product_cards[0])}")
    print(f"   first card keys: {list(result.product_cards[0].keys()) if isinstance(result.product_cards[0], dict) else dir(result.product_cards[0])}")

# Try model_to_dict from tool_handlers
try:
    from rag.recommendation.tool_handlers import model_to_dict
    payload = model_to_dict(result)
    print(f"   payload keys: {list(payload.keys())}")
    print(f"   payload product_cards count: {len(payload.get('product_cards', []))}")
except ImportError:
    print("   model_to_dict not in tool_handlers, checking other imports...")

# Try sanitize_result_for_response
response_payload = sanitize_result_for_response(payload)
print(f"7. sanitize_result_for_response:")
print(f"   response product_cards count: {len(response_payload.get('product_cards', []))}")

# Step 6: Check trace for clues
trace = result.trace
print(f"\n--- Trace ---")
print(f"no_match_reason: {trace.get('no_match_reason', '')}")
print(f"clarification_required: {trace.get('clarification_required', False)}")
print(f"catalog_guard_result: {trace.get('catalog_guard_result', '')}")
sf = trace.get("structured_filter", {})
if sf:
    for cat, diag in sf.items():
        print(f"\n  Filter [{cat}]:")
        for k in ("raw_count", "after_stock_count", "after_budget_count", "returned_count"):
            print(f"    {k}: {diag.get(k, '?')}")
