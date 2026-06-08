"""Re-run previously FAIL/PARTIAL cases with full Q&A capture."""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from test_agent_v1 import send_chat_stream

RETEST_CASES = [
    (114, "有没有防水的运动手表", "recommend → 告知无运动手表或推荐替代"),
    (120, "有没有好看的裙子", "recommend → 告知无裙装或推荐替代"),
    (125, "3000到5000之间的手机", "recommend(budget=5000, 手机)+CARD"),
    (126, "第二页的商品", "追问是哪个品类的第二页"),
    (138, "运动跑鞋怎么选择尺码", "recommend → 跑鞋尺码建议"),
    (141, "这款耳机有差评吗", "general_chat: 追问是哪款耳机（无上下文）"),
    (147, "推荐耳机，不要华为的，500到2000之间", "recommend(exclude=华为, 耳机, budget)+CARD"),
    (166, "iPhone 17 Pro 只要 999 对吧？", "纠正价格：实际 8999 元"),
    (167, "三星Galaxy S30怎么样", "诚实告知没有该商品"),
]


def run_case(case_id, query, expected):
    session_id = f"retest_{case_id}_v5_mimo_fix"
    resp = send_chat_stream(query, session_id=session_id)
    events = resp["events"]

    full_text = ""
    tool_name = ""
    tool_args = {}
    cards = []
    routing_trace = {}
    follow_ups = []

    for evt in events:
        etype = evt.get("event", "")
        edata = evt.get("data", {})
        if etype == "tool_call":
            tool_name = edata.get("name", "")
            tool_args = edata.get("arguments", {})
            routing_trace = edata.get("routing_trace") or {}
        elif etype == "delta":
            full_text += edata.get("text", "")
        elif etype == "product_cards":
            cards = edata if isinstance(edata, list) else edata.get("products", edata.get("cards", []))
        elif etype == "follow_up_questions":
            follow_ups = edata.get("questions") or []

    return {
        "case_id": case_id,
        "query": query,
        "expected": expected,
        "tool": tool_name,
        "tool_args": tool_args,
        "cards": cards,
        "card_count": len(cards),
        "card_ids": [c.get("product_id") for c in cards],
        "full_text": full_text,
        "follow_ups": follow_ups,
        "routing_source": routing_trace.get("router_final_source", ""),
        "clarification": (routing_trace.get("final") or {}).get("arguments", {}).get("clarification_question", ""),
    }


def main():
    results = []
    for case_id, query, expected in RETEST_CASES:
        print(f"\n{'='*70}")
        print(f"  #{case_id}: \"{query}\"")
        print(f"  预期: {expected}")
        print(f"{'='*70}")

        r = run_case(case_id, query, expected)
        results.append(r)

        print(f"  工具: {r['tool']}")
        print(f"  路由来源: {r['routing_source']}")
        budget = r['tool_args'].get('budget')
        pmin = r['tool_args'].get('price_min')
        pmax = r['tool_args'].get('price_max')
        excl = r['tool_args'].get('exclude_brands', [])
        cat = r['tool_args'].get('category', '')
        print(f"  budget={budget}  price_min={pmin}  price_max={pmax}  category={cat}  exclude_brands={excl}")
        print(f"  商品卡数: {r['card_count']}  ids={r['card_ids'][:5]}")
        print(f"  follow_ups: {r['follow_ups']}")
        print(f"  完整回复:")
        for line in r['full_text'].split('\n'):
            if line.strip():
                print(f"    > {line.strip()}")

    # Save
    out_path = os.path.join(os.path.dirname(__file__), "..", "reports", "retest_results_v4_mimo.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n\nResults saved to {out_path}")


if __name__ == "__main__":
    main()
