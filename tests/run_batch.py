"""run_batch.py - Run a batch of test cases and flag FAIL/PARTIAL."""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from test_agent_v1 import ALL_CASES, run_test_case, check_server_health

def evaluate(case, result):
    """Simple heuristic PASS/PARTIAL/FAIL evaluation."""
    if result.error:
        return "ERR"
    tool = result.tool_calls[0].get("name", "") if result.tool_calls else ""
    expected = case.expected_behavior
    cards = len(result.product_cards)
    has_compare = result.comparison_table is not None and len((result.comparison_table or {}).get("rows", [])) > 0
    has_cart = result.cart is not None

    # Basic conversation: no tool call expected
    if "不调工具" in expected:
        return "PASS" if tool in ("general_chat", "") else "PARTIAL"
    if "友好回应" in expected or "礼貌拒绝" in expected or "拒绝" in expected:
        return "PASS" if tool == "general_chat" else "PARTIAL"

    # recommend expected
    if "recommend" in expected.lower() or "CARD" in expected:
        if tool != "recommend_shopping_products":
            return "FAIL"
        if cards > 0:
            return "PASS"
        elif has_compare:
            return "PARTIAL"
        else:
            return "FAIL"

    # compare expected
    if "compare_products" in expected or "对比表" in expected:
        if tool == "compare_products" or has_compare:
            return "PASS"
        return "FAIL"

    # cart expected
    if "cart" in expected.lower() or "购物车" in expected:
        if tool == "apply_cart_instruction" or has_cart:
            return "PASS"
        return "PARTIAL"

    # general
    if cards > 0 or has_compare:
        return "PASS"
    if tool:
        return "PARTIAL"
    return "FAIL"

def main():
    start = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    end = int(sys.argv[2]) if len(sys.argv) > 2 else start

    if not check_server_health():
        print("Server down!"); sys.exit(1)

    cases = [c for c in ALL_CASES if start <= c.id <= end]
    if not cases:
        print(f"No cases in range {start}-{end}")
        return

    results = []
    for c in cases:
        r = run_test_case(c)
        verdict = evaluate(c, r)
        cards = len(r.product_cards)
        tool = r.tool_calls[0].get("name", "") if r.tool_calls else "none"
        resp = r.text_response[:80].replace("\n", " ")
        print(f"#{c.id:3d} [{verdict:7s}] {c.input_text[:30]:30s} | tool={tool:35s} | cards={cards} | {resp}...")
        results.append((c, r, verdict))

    # Summary
    pass_n = sum(1 for _,_,v in results if v == "PASS")
    part_n = sum(1 for _,_,v in results if v == "PARTIAL")
    fail_n = sum(1 for _,_,v in results if v == "FAIL")
    err_n = sum(1 for _,_,v in results if v == "ERR")
    print(f"\n--- Batch {start}-{end}: PASS={pass_n} PARTIAL={part_n} FAIL={fail_n} ERR={err_n} ---")

    # Print FAIL/PARTIAL details
    for c, r, v in results:
        if v in ("FAIL", "PARTIAL", "ERR"):
            print(f"\n  #{c.id} [{v}] \"{c.input_text}\"")
            print(f"  Expected: {c.expected_behavior}")
            print(f"  Tool: {r.tool_chain_str}")
            print(f"  Response: {r.text_response[:200]}")
            if r.error:
                print(f"  Error: {r.error}")

if __name__ == "__main__":
    main()
