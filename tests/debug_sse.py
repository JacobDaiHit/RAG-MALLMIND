"""debug_sse.py - Direct SSE call to the API and inspect all events."""
import sys, json, os, requests
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

query = sys.argv[1] if len(sys.argv) > 1 else "500元以下的零食"
print(f"Query: {query}")

url = "http://localhost:8000/api/chat/stream"
payload = {"message": query, "session_id": "debug_sse_test"}
headers = {"Content-Type": "application/json"}

resp = requests.post(url, json=payload, headers=headers, stream=True, timeout=30)
print(f"Status: {resp.status_code}")
print()

for line in resp.iter_lines(decode_unicode=True):
    if not line or not line.startswith("data: "):
        continue
    raw = line[6:]
    if raw.strip() == "[DONE]":
        break
    try:
        evt = json.loads(raw)
    except json.JSONDecodeError:
        print(f"  [raw] {raw[:100]}")
        continue

    etype = evt.get("event", "?")
    edata = evt.get("data", {})

    if etype == "product_cards":
        cards = edata if isinstance(edata, list) else edata.get("products", edata.get("cards", []))
        print(f"[product_cards] count={len(cards)}")
        for c in cards[:5]:
            print(f"  [{c.get('product_id')}] {c.get('title', '?')[:50]} | {c.get('price')}")
    elif etype == "tool_call":
        args = edata.get("arguments", {})
        print(f"[tool_call] name={edata.get('name')} conf={edata.get('confidence')} src={edata.get('source')}")
        print(f"  args: query={args.get('query')} budget={args.get('budget')} category={args.get('category')}")
    elif etype == "runtime_mode":
        print(f"[runtime_mode] mode={edata.get('selected_mode')}")
    elif etype == "delta":
        text = edata.get("text", "")
        if len(text) > 10:
            print(f"[delta] {text[:80]}...")
    elif etype == "progress":
        pass
    elif etype == "result":
        pc = edata.get("product_cards", [])
        print(f"[result] product_cards count={len(pc)}")
    elif etype == "validation_error":
        print(f"[validation_error] {edata}")
    elif etype == "candidate_scope":
        print(f"[candidate_scope] {json.dumps(edata, ensure_ascii=False)[:100]}")
    elif etype == "done":
        print(f"[done]")
    else:
        print(f"[{etype}] {json.dumps(edata, ensure_ascii=False)[:100]}")
