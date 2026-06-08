"""Diagnostic: re-run case #126 and dump full routing trace + response."""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from test_agent_v1 import send_chat_stream, _parse_sse_event

def main():
    query = "第二页的商品"
    session_id = "diag_126_v2"
    print(f"=== Diagnostic: case #126 ===")
    print(f"Query: {query}")
    print(f"Session: {session_id}\n")

    resp = send_chat_stream(query, session_id=session_id)
    events = resp["events"]

    full_text = ""
    tool_calls = []
    routing_trace = {}
    all_events = []

    for evt in events:
        etype = evt.get("event", "")
        edata = evt.get("data", {})
        all_events.append({"event": etype, "data": edata})

        if etype == "tool_call":
            tool_calls.append(edata)
            routing_trace = edata.get("routing_trace") or {}
        elif etype == "delta":
            full_text += edata.get("text", "")
        elif etype == "product_cards":
            cards = edata if isinstance(edata, list) else edata.get("products", edata.get("cards", []))
            print(f"[product_cards] count={len(cards)}")
            for c in cards[:5]:
                print(f"  - {c.get('product_id')} | {c.get('title', '')[:40]} | {c.get('brand', '')} | {c.get('price', '?')}")
        elif etype == "runtime_mode":
            print(f"[runtime_mode] {edata.get('selected_mode')}")

    print(f"\n--- Tool Calls ---")
    for tc in tool_calls:
        print(f"  name: {tc.get('name')}")
        print(f"  args: {json.dumps(tc.get('arguments', {}), ensure_ascii=False)}")
        rt = tc.get("routing_trace", {})
        print(f"  routing_trace keys: {list(rt.keys())}")
        # Print full routing trace
        print(f"  routing_trace (full):")
        print(json.dumps(rt, ensure_ascii=False, indent=4))

    print(f"\n--- Full Text Response ---")
    print(full_text)

    # Save raw JSON
    out_path = os.path.join(os.path.dirname(__file__), "..", "reports", "diag_126_raw.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "query": query,
            "session_id": session_id,
            "tool_calls": tool_calls,
            "routing_trace": routing_trace,
            "full_text": full_text,
            "all_events_count": len(all_events),
        }, f, ensure_ascii=False, indent=2)
    print(f"\nRaw saved to {out_path}")

if __name__ == "__main__":
    main()
