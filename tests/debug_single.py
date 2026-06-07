"""debug_single.py - Run a single test case with verbose debug output.

Usage: python tests/debug_single.py <case_id>
Example: python tests/debug_single.py 6
"""
from __future__ import annotations
import json, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from test_agent_v1 import ALL_CASES, run_test_case, check_server_health

def main():
    if len(sys.argv) < 2:
        print("Usage: python tests/debug_single.py <case_id>")
        sys.exit(1)

    case_id = int(sys.argv[1])
    case = None
    for c in ALL_CASES:
        if c.id == case_id:
            case = c
            break

    if not case:
        print(f"Case #{case_id} not found!")
        sys.exit(1)

    print(f"=== Case #{case.id} [{case.category}] ===")
    print(f"Input: {case.input_text}")
    print(f"Expected: {case.expected_behavior}")
    print(f"Session: {case.session_id}")
    print()

    if not check_server_health():
        print("Server unreachable!")
        sys.exit(1)

    result = run_test_case(case)

    print(f"--- Result ---")
    print(f"Runtime mode: {result.runtime_mode}")
    print(f"Tool chain: {result.tool_chain_str}")
    print(f"Events: {result.events_received}")
    print(f"Elapsed: {result.elapsed_ms}ms")
    print()

    # Routing trace details
    rt = result.routing_trace
    if rt:
        print(f"--- Routing Trace ---")
        print(f"  runtime_mode: {rt.get('runtime_mode')}")
        print(f"  local_scores: {json.dumps(rt.get('local_scores', {}), ensure_ascii=False)}")
        print(f"  llm_tool: {rt.get('llm_tool')}")
        print(f"  llm_confidence: {rt.get('llm_confidence')}")
        print(f"  guard_tool: {rt.get('guard_tool')}")
        print(f"  final_tool: {rt.get('final_tool')}")
        print(f"  final_source: {rt.get('final_source')}")
        print(f"  _llm_chosen: {rt.get('_llm_chosen')}")
        print()

    # Tool call arguments
    for tc in result.tool_calls:
        print(f"--- Tool Call ---")
        print(f"  name: {tc.get('name')}")
        print(f"  confidence: {tc.get('confidence')}")
        print(f"  source: {tc.get('source')}")
        args = tc.get("arguments", {})
        print(f"  args: {json.dumps(args, ensure_ascii=False, indent=4)}")
        print()

    # Product cards
    if result.product_cards:
        print(f"--- Product Cards ({len(result.product_cards)}) ---")
        for card in result.product_cards:
            pid = card.get("product_id", "?")
            title = card.get("title", "?")[:60]
            price = card.get("price", "?")
            cat = card.get("category", "?")
            score = card.get("score", card.get("_score", "?"))
            print(f"  [{pid}] {title} | price={price} | cat={cat} | score={score}")
    else:
        print("--- Product Cards: EMPTY ---")
    print()

    # Comparison table
    if result.comparison_table:
        rows = result.comparison_table.get("rows", [])
        print(f"--- Comparison Table ({len(rows)} rows) ---")
        for row in rows[:5]:
            title = row.get("title", row.get("product_title", "?"))[:50]
            pid = row.get("product_id", "?")
            print(f"  [{pid}] {title}")
    print()

    # Cart
    if result.cart:
        items = (result.cart or {}).get("items", [])
        print(f"--- Cart ({len(items)} items) ---")
        for item in items:
            print(f"  {json.dumps(item, ensure_ascii=False)[:100]}")
    print()

    # Text response
    print(f"--- Response ---")
    print(result.text_response[:500])
    print()

    # Error
    if result.error:
        print(f"--- ERROR ---")
        print(result.error)

if __name__ == "__main__":
    main()
