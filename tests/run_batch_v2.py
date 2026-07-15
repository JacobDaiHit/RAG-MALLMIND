"""run_batch_v2.py - Run all v2 test cases and flag PASS/PARTIAL/FAIL."""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from test_agent_v1 import check_server_health
from test_agent_v2 import ALL_CASES_V2, run_test_case_v2


def evaluate_v2(case, result):
    """Heuristic PASS/PARTIAL/FAIL evaluation for v2 cases."""
    if result.error:
        return "ERR"
    tool = result.tool_calls[0].get("name", "") if result.tool_calls else ""
    expected = case.expected_behavior
    cards = len(result.product_cards)
    has_compare = result.comparison_table is not None and len((result.comparison_table or {}).get("rows", [])) > 0
    has_cart = result.cart is not None
    resp = result.text_response

    # Basic conversation: no tool call expected
    if "不调工具" in expected:
        return "PASS" if tool in ("general_chat", "") else "PARTIAL"
    if "友好回应" in expected or "礼貌拒绝" in expected or "拒绝" in expected:
        return "PASS" if tool == "general_chat" else "PARTIAL"
    if "追问" in expected and "第二页" in case.input_text:
        return "PASS" if tool == "general_chat" else "PARTIAL"
    if "追问" in expected and "哪款" in expected:
        return "PASS" if tool == "general_chat" else "PARTIAL"

    # Anti-hallucination
    if "诚实告知" in expected or "诚实回答" in expected or "没有" in expected:
        if "没有该商品" in expected or "没有PS5" in expected or "没有超高价" in expected or "不知道" in expected:
            return "PASS" if cards == 0 else "PARTIAL"
        if "纠正价格" in expected:
            if "8999" in resp or "实际" in resp:
                return "PASS"
            return "PARTIAL"
        return "PASS" if tool == "general_chat" else "PARTIAL"

    # recommend expected
    if "recommend" in expected.lower() or "CARD" in expected:
        if tool != "recommend_shopping_products":
            if cards > 0:
                return "PARTIAL"
            return "FAIL"
        if cards > 0:
            return "PASS"
        elif has_compare:
            return "PARTIAL"
        else:
            return "FAIL"

    # compare expected
    if "compare_products" in expected or "对比表" in expected or "对比" in expected:
        if tool == "compare_products" or has_compare:
            return "PASS"
        return "FAIL"

    # cart expected
    if "cart" in expected.lower() or "购物车" in expected:
        if tool == "apply_cart_instruction" or has_cart:
            return "PASS"
        return "PARTIAL"

    # context continuation
    if "理解上下文" in expected:
        if cards > 0 or resp:
            return "PASS"
        return "PARTIAL"

    # PC build plan
    if "pc_build_plan" in expected.lower():
        if result.pc_build_plan:
            return "PASS"
        return "FAIL"

    # General info responses
    if "介绍" in expected or "告知" in expected or "说明" in expected or "建议" in expected:
        if resp and len(resp) > 20:
            return "PASS"
        return "PARTIAL"

    # general fallback
    if cards > 0 or has_compare:
        return "PASS"
    if tool:
        return "PARTIAL"
    if resp and len(resp) > 20:
        return "PARTIAL"
    return "FAIL"


def main():
    start = int(sys.argv[1]) if len(sys.argv) > 1 else 101
    end = int(sys.argv[2]) if len(sys.argv) > 2 else 999

    if not check_server_health():
        print("Server down!"); sys.exit(1)

    cases = [c for c in ALL_CASES_V2 if start <= c.id <= end]
    if not cases:
        print(f"No cases in range {start}-{end}")
        return

    results = []
    for c in cases:
        r = run_test_case_v2(c)
        verdict = evaluate_v2(c, r)
        cards = len(r.product_cards)
        tool = r.tool_calls[0].get("name", "") if r.tool_calls else "none"
        resp = r.text_response[:80].replace("\n", " ")
        print(f"#{c.id:3d} [{verdict:7s}] {c.input_text[:30]:30s} | tool={tool:35s} | cards={cards} | {resp}...")
        results.append((c, r, verdict))

    # Summary
    pass_n = sum(1 for _, _, v in results if v == "PASS")
    part_n = sum(1 for _, _, v in results if v == "PARTIAL")
    fail_n = sum(1 for _, _, v in results if v == "FAIL")
    err_n = sum(1 for _, _, v in results if v == "ERR")
    total = len(results)
    weighted = pass_n + part_n * 0.5
    pct = weighted / total * 100 if total else 0
    print(f"\n{'='*60}")
    print(f"  Total={total}  PASS={pass_n}  PARTIAL={part_n}  FAIL={fail_n}  ERR={err_n}")
    print(f"  Weighted pass rate: {pct:.1f}%")
    print(f"{'='*60}")

    # Print FAIL/PARTIAL details
    for c, r, v in results:
        if v in ("FAIL", "PARTIAL", "ERR"):
            print(f"\n  #{c.id} [{v}] \"{c.input_text}\"")
            print(f"  Expected: {c.expected_behavior}")
            print(f"  Tool: {r.tool_chain_str}")
            print(f"  Response: {r.text_response[:300]}")
            if r.product_cards:
                ids = [p.get('product_id', '?') for p in r.product_cards[:5]]
                print(f"  Cards: {ids}")
            if r.error:
                print(f"  Error: {r.error}")

    # Save JSON results for report generation
    out_dir = os.path.join(os.path.dirname(__file__), "..", ".pytest_tmp")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "v2_llm_filter_test_results.json")
    payload = []
    for c, r, v in results:
        payload.append({
            "case_id": c.id,
            "category": c.category,
            "input": c.input_text,
            "expected": c.expected_behavior,
            "verdict": v,
            "tool": r.tool_calls[0].get("name", "") if r.tool_calls else "",
            "card_count": len(r.product_cards),
            "response_preview": r.text_response[:200],
            "elapsed_ms": r.elapsed_ms,
            "error": r.error,
        })
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()
