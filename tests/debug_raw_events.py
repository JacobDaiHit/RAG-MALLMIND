"""debug_raw_events.py - Show raw SSE events for a query."""
import sys, json, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from test_agent_v1 import send_chat_stream

query = sys.argv[1] if len(sys.argv) > 1 else "500元以下的零食"
print(f"Query: {query}")
print()

response = send_chat_stream(query, session_id="debug_raw_test")
events = response["events"]

print(f"Total events: {len(events)}")
for i, evt in enumerate(events):
    etype = evt.get("event", "?")
    edata = evt.get("data", {})
    
    if etype == "product_cards":
        cards = edata if isinstance(edata, list) else edata.get("products", edata.get("cards", []))
        print(f"\n[{i}] product_cards: count={len(cards)}")
        for c in cards[:3]:
            pid = c.get("product_id", "?")
            title = c.get("title", "?")[:40]
            print(f"  [{pid}] {title}")
    elif etype == "tool_call":
        args = edata.get("arguments", {})
        print(f"[{i}] tool_call: name={edata.get('name')} conf={edata.get('confidence')} src={edata.get('source')}")
    elif etype == "delta":
        text = edata.get("text", "")
        print(f"[{i}] delta: {text[:60]}...")
    elif etype == "progress":
        label = edata.get("label", "")
        print(f"[{i}] progress: {label}")
    elif etype == "runtime_mode":
        print(f"[{i}] runtime_mode: {edata.get('selected_mode')}")
    elif etype == "result":
        pc = edata.get("product_cards", [])
        print(f"[{i}] result: product_cards={len(pc)}")
    else:
        summary = json.dumps(edata, ensure_ascii=False)[:80]
        print(f"[{i}] {etype}: {summary}")
