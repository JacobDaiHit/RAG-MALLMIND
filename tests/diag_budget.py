"""Budget diagnostic: run specific cases and dump full Q&A + routing trace."""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from test_agent_v1 import send_chat_stream

BUDGET_CASES = [
    (122, "500元以下的商品有哪些"),
    (125, "3000到5000之间的手机"),
    (147, "推荐耳机，不要华为的，500到2000之间"),
    (170, "高端护肤品送妈妈，预算3000以内"),
]

def run_one(case_id, query):
    session_id = f"budget_diag_{case_id}"
    resp = send_chat_stream(query, session_id=session_id)
    events = resp["events"]

    full_text = ""
    tool_args = {}
    tool_name = ""
    cards = []

    for evt in events:
        etype = evt.get("event", "")
        edata = evt.get("data", {})
        if etype == "tool_call":
            tool_name = edata.get("name", "")
            tool_args = edata.get("arguments", {})
        elif etype == "delta":
            full_text += edata.get("text", "")
        elif etype == "product_cards":
            cards = edata if isinstance(edata, list) else edata.get("products", edata.get("cards", []))

    return tool_name, tool_args, cards, full_text


def main():
    results = []
    for case_id, query in BUDGET_CASES:
        print(f"\n{'='*70}")
        print(f"  #{case_id}: \"{query}\"")
        print(f"{'='*70}")

        tool_name, args, cards, text = run_one(case_id, query)

        budget = args.get("budget")
        price_min = args.get("price_min")
        price_max = args.get("price_max")
        category = args.get("category", "")
        exclude_brands = args.get("exclude_brands", [])

        print(f"  Tool: {tool_name}")
        print(f"  budget={budget}  price_min={price_min}  price_max={price_max}")
        print(f"  category={category}  exclude_brands={exclude_brands}")
        print(f"  Cards: {len(cards)}")
        for c in cards[:5]:
            pid = c.get("product_id", "?")
            title = c.get("title", "")[:40]
            price = c.get("price", "?")
            print(f"    - {pid} | {title} | ¥{price}")
        print(f"  Response: {text[:200]}")

        results.append({
            "case_id": case_id,
            "query": query,
            "tool": tool_name,
            "budget": budget,
            "price_min": price_min,
            "price_max": price_max,
            "category": category,
            "exclude_brands": exclude_brands,
            "card_count": len(cards),
            "card_ids": [c.get("product_id") for c in cards],
            "response": text,
        })

    # Save
    out_path = os.path.join(os.path.dirname(__file__), "..", "reports", "budget_diag_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()
