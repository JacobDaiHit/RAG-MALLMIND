"""Diagnose mimo LLM router raw output vs rule-based extraction.

For each test case, we:
1. Run local_route_tool_call (rule-based)
2. Check should_skip_llm_route
3. Call try_llm_route_tool_call directly (bypassing skip)
4. Run merge_route_arguments and validate_and_guard
5. Compare raw LLM output vs final merged args
"""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv()

# Force module reload
import importlib
import rag.recommendation.llm_client as llm_client_mod
import rag.recommendation.tool_router as tr_mod
import rag.recommendation.recommendation_pipeline as rp_mod
importlib.reload(llm_client_mod)
importlib.reload(tr_mod)
importlib.reload(rp_mod)

from rag.recommendation.tool_router import (
    local_route_tool_call,
    should_skip_llm_route,
    try_llm_route_tool_call,
    merge_route_arguments,
    extract_slots_rule_based,
    validate_and_guard_tool_call,
    _runtime_mode_from_session,
)
from rag.recommendation.llm_client import get_llm_provider_trace, OpenAICompatibleChatClient

from rag.recommendation.session_state import ShoppingSession

def make_session():
    return ShoppingSession(session_id="diag_mimo")

DIAG_CASES = [
    (125, "3000到5000之间的手机"),
    (126, "第二页的商品"),
    (141, "这款耳机有差评吗"),
    (147, "推荐耳机，不要华为的，500到2000之间"),
    (166, "iPhone 17 Pro 只要 999 对吧？"),
    (167, "三星Galaxy S30怎么样"),
]


def run_diag(case_id, query):
    session = make_session()
    print(f"\n{'='*80}")
    print(f"  #{case_id}: \"{query}\"")
    print(f"{'='*80}")

    # --- Provider info ---
    provider = get_llm_provider_trace()
    print(f"\n[Provider] {provider['llm_provider']} / router={provider['router_model']} / parse={provider['parse_model']}")

    # --- Step 1: Local route ---
    local = local_route_tool_call(query, session)
    local_name = local.get("name", "")
    local_args = local.get("arguments", {})
    local_scores = local.get("route_scores", {})
    print(f"\n[1] Local route: {local_name}")
    print(f"    confidence={local_scores.get('confidence')} margin={local_scores.get('margin')}")
    print(f"    budget={local_args.get('budget')} price_min={local_args.get('price_min')} price_max={local_args.get('price_max')}")
    print(f"    category={local_args.get('category')} brands={local_args.get('brands')} exclude_brands={local_args.get('exclude_brands')}")

    # --- Step 2: Should skip? ---
    skip = should_skip_llm_route(query, session, local)
    mode = _runtime_mode_from_session(session)
    print(f"\n[2] should_skip_llm_route = {skip} (mode={mode})")

    # --- Step 3: Force LLM call ---
    print(f"\n[3] Forcing LLM router call (bypassing skip)...")
    llm_call, failure_reason = try_llm_route_tool_call(query, session)
    if llm_call is None:
        print(f"    LLM FAILED: {failure_reason}")
    else:
        llm_name = llm_call.get("name", "")
        llm_args = llm_call.get("arguments", {})
        llm_conf = llm_call.get("confidence", 0)
        print(f"    LLM result: {llm_name} (confidence={llm_conf})")
        print(f"    budget={llm_args.get('budget')} price_min={llm_args.get('price_min')} price_max={llm_args.get('price_max')}")
        print(f"    category={llm_args.get('category')} brands={llm_args.get('brands')} exclude_brands={llm_args.get('exclude_brands')}")
        print(f"    sort_order={llm_args.get('sort_order')} must_have_terms={llm_args.get('must_have_terms')}")
        print(f"    source={llm_call.get('source')} _llm_chosen would be set if conf >= threshold")

        # --- Step 4: Merge ---
        rule_slots = extract_slots_rule_based(query)
        merged = merge_route_arguments(llm_args, rule_slots)
        print(f"\n[4] After merge (LLM + rule_slots):")
        print(f"    budget={merged.get('budget')} price_min={merged.get('price_min')} price_max={merged.get('price_max')}")
        print(f"    category={merged.get('category')} brands={merged.get('brands')} exclude_brands={merged.get('exclude_brands')}")

        # --- Step 5: Confidence comparison ---
        _llm_conf = float(llm_conf or 0)
        _local_name = local_name
        if llm_name == "general_chat" and _local_name != "general_chat":
            chosen_is_llm = _llm_conf >= 0.80
        else:
            chosen_is_llm = _llm_conf >= 0.50
        print(f"\n[5] Confidence comparison: LLM={_llm_conf} vs local conf={local_scores.get('confidence')}")
        print(f"    LLM chosen? {chosen_is_llm} (_llm_chosen={chosen_is_llm})")

    # --- Step 6: Check parse_requirement ---
    print(f"\n[6] Checking parse_requirement for clarification_question...")
    req = rp_mod.parse_requirement(query, use_llm=True)
    print(f"    clarification_question = \"{req.clarification_question}\"")
    print(f"    brands={req.brands} excluded_brands={req.excluded_brands}")
    print(f"    price_min={req.price_min} price_max={req.price_max}")
    print(f"    must_have_terms={req.must_have_terms} excluded_terms={req.excluded_terms}")

    # Also check raw LLM parse output
    print(f"\n[7] Raw parse LLM output test...")
    client = OpenAICompatibleChatClient()
    if client.configured:
        from rag.recommendation.recommendation_pipeline import build_requirement_prompt, parse_requirement_rule_based
        rule_req = parse_requirement_rule_based(query)
        prompt = build_requirement_prompt(query, rule_req)
        try:
            import os as _os
            raw = client.chat_json(
                [
                    {"role": "system", "content": "你是传统电商 AI 导购的需求理解器。只输出 JSON，不要解释。"},
                    {"role": "user", "content": prompt},
                ],
                model=_os.getenv("MALLMIND_PARSE_MODEL") or client.config.fast_model,
                temperature=0.1,
                max_tokens=1200,
            )
            cq = raw.get("clarification_question", "")
            print(f"    RAW clarification_question = \"{cq}\"")
            print(f"    RAW excluded_brands = {raw.get('excluded_brands')}")
            print(f"    RAW price_min = {raw.get('price_min')}, price_max = {raw.get('price_max')}")
        except Exception as e:
            print(f"    Parse LLM call failed: {e}")
    else:
        print(f"    LLM not configured")


def main():
    for case_id, query in DIAG_CASES:
        try:
            run_diag(case_id, query)
        except Exception as e:
            print(f"\n  ERROR on #{case_id}: {e}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    main()
