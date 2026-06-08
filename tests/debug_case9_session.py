"""Debug Case 9 session context flow"""
import json, requests, sys, io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
BASE = "http://127.0.0.1:8000/api/chat/stream"

def send_and_get(session_id, message):
    payload = {"message": message, "session_id": session_id, "catalog_scope": "ecommerce"}
    r = requests.post(BASE, json=payload, stream=True, timeout=120)
    r.raise_for_status()
    raw_text = r.content.decode("utf-8", errors="replace")
    events_raw = raw_text.split("\n\n")
    for event_block in events_raw:
        event_type = ""
        data_lines = []
        for line in event_block.split("\n"):
            if line.startswith("event:"):
                event_type = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
            elif data_lines:
                data_lines.append(line.strip())
        if not data_lines:
            continue
        data_str = "".join(data_lines)
        if data_str in ("", "[DONE]"):
            continue
        try:
            ev = json.loads(data_str)
        except:
            continue
        if event_type == "result":
            req = (ev.get("requirement") or {})
            print(f"  RESULT target_sub_categories: {req.get('target_sub_categories')}")
            print(f"  RESULT desired_categories: {req.get('desired_categories')}")
            print(f"  RESULT must_have_terms: {req.get('must_have_terms')}")
            cards = ev.get("product_cards") or []
            print(f"  RESULT cards: {len(cards)}")
            for c in cards[:3]:
                print(f"    {c.get('brand')} | {str(c.get('title',''))[:40]} | price={c.get('price')}")
        elif event_type == "candidate_scope":
            filters = ev.get("active_filters") or {}
            print(f"  SCOPE target_sub_categories: {filters.get('target_sub_categories')}")
            by_cat = ev.get("by_category") or {}
            for cat, info in by_cat.items():
                if isinstance(info, dict):
                    print(f"  SCOPE [{cat}]: raw={info.get('raw_count')} excl={info.get('after_exclusion_count')}")

SID = "debug_case9"

print("=== Turn 1 ===")
send_and_get(SID, "我最近在减肥，想买个无糖的气泡水。")

print("\n=== Turn 2 ===")
send_and_get(SID, "白桃味的喝腻了，有没有其他口味？")
