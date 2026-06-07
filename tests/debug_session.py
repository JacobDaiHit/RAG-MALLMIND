"""debug_session.py - Test with empty session_id vs named session_id."""
import sys, json, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from test_agent_v1 import send_chat_stream

query = sys.argv[1] if len(sys.argv) > 1 else "500元以下的零食"

# Test 1: empty session_id (like batch runner)
print(f"=== Test with session_id='' ===")
resp1 = send_chat_stream(query, session_id="")
events1 = resp1["events"]
cards1 = []
for evt in events1:
    if evt.get("event") == "product_cards":
        edata = evt.get("data", {})
        cards1 = edata if isinstance(edata, list) else edata.get("products", edata.get("cards", []))
print(f"  Events: {len(events1)}, Cards: {len(cards1)}")
for c in cards1[:3]:
    print(f"  [{c.get('product_id')}] {c.get('title', '?')[:40]}")

# Test 2: named session_id
print(f"\n=== Test with session_id='test_named' ===")
resp2 = send_chat_stream(query, session_id="test_named")
events2 = resp2["events"]
cards2 = []
for evt in events2:
    if evt.get("event") == "product_cards":
        edata = evt.get("data", {})
        cards2 = edata if isinstance(edata, list) else edata.get("products", edata.get("cards", []))
print(f"  Events: {len(events2)}, Cards: {len(cards2)}")
for c in cards2[:3]:
    print(f"  [{c.get('product_id')}] {c.get('title', '?')[:40]}")
