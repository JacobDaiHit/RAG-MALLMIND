"""Case 4 & Case 9 深度追踪：验证 session 上下文修复效果"""
import json, requests, sys, io, time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:8000/api/chat/stream"

def send_and_trace(session_id, message):
    payload = {"message": message, "session_id": session_id, "catalog_scope": "ecommerce"}
    r = requests.post(BASE, json=payload, stream=True, timeout=120)
    r.raise_for_status()
    raw_text = r.content.decode("utf-8", errors="replace")
    events_raw = raw_text.split("\n\n")
    parsed_events = []
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
        parsed_events.append({"event": event_type, "data": ev})
    return parsed_events

def trace_turn(session_id, message, turn_num):
    print(f"\n{'='*80}")
    print(f"TURN {turn_num}: {message}")
    print(f"{'='*80}")
    events = send_and_trace(session_id, message)
    for i, evt in enumerate(events):
        etype = evt["event"]
        edata = evt["data"]
        if etype == "runtime_mode":
            print(f"  [{i}] MODE: {edata.get('selected_mode')} | {edata.get('reason','')[:60]}")
        elif etype == "tool_call":
            args = edata.get("arguments") or {}
            print(f"  [{i}] TOOL: {edata.get('name')} src={edata.get('source')} conf={edata.get('confidence')}")
            print(f"       query: {str(args.get('query',''))[:70]}")
            print(f"       category: {args.get('category')} | budget: {args.get('budget')}")
            print(f"       must_have: {args.get('must_have_terms')} | exclude_brands: {args.get('exclude_brands')}")
        elif etype == "delta":
            text = edata.get("text", "")
            if text:
                print(f"  [{i}] DELTA: {text[:120]}")
        elif etype == "product_cards":
            products = edata if isinstance(edata, list) else edata.get("products", [])
            print(f"\n  [{i}] CARDS ({len(products)}):")
            for j, p in enumerate(products[:5]):
                print(f"       [{j+1}] {p.get('product_id','')} | {p.get('brand','')} | {str(p.get('title',''))[:40]} | price={p.get('price','?')}")
        elif etype == "candidate_scope":
            filters = edata.get("active_filters") or {}
            by_cat = edata.get("by_category") or {}
            print(f"\n  [{i}] SCOPE: categories={filters.get('categories')} price_max={filters.get('price_max')}")
            for cat, info in by_cat.items():
                if isinstance(info, dict):
                    print(f"       [{cat}]: raw={info.get('raw_count')} excl={info.get('after_exclusion_count')} budget={info.get('within_budget_count')}")
        elif etype == "result":
            req = edata.get("requirement") or {}
            print(f"\n  [{i}] RESULT:")
            print(f"       desired_categories: {req.get('desired_categories')}")
            print(f"       target_sub_categories: {req.get('target_sub_categories')}")
            print(f"       must_have_terms: {req.get('must_have_terms')}")
            print(f"       brands: {req.get('brands')} | excluded_brands: {req.get('excluded_brands')}")
            print(f"       price_max: {req.get('price_max')}")
        elif etype == "done":
            print(f"  [{i}] DONE")
    return events

# ============================================================
# Case 4: 游戏PC（2轮）
# ============================================================
print("#" * 80)
print("# CASE 4: 游戏PC（2轮）")
print("#" * 80)

SID4 = "trace_case4"
trace_turn(SID4, "我想配一台能玩黑神话悟空的电脑，预算8000左右。", 1)
trace_turn(SID4, "CPU要Intel的，不要AMD。", 2)

# ============================================================
# Case 9: 气泡水（3轮）
# ============================================================
print("\n\n" + "#" * 80)
print("# CASE 9: 气泡水（3轮）")
print("#" * 80)

SID9 = "trace_case9"
trace_turn(SID9, "我最近在减肥，想买个无糖的气泡水。", 1)
trace_turn(SID9, "白桃味的喝腻了，有没有其他口味？", 2)
trace_turn(SID9, "哪个口味评价最好？", 3)

print("\n\nTRACE COMPLETE")
