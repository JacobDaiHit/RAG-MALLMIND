"""Case 12 v4: 验证 PC 装机 + compare_products"""
import json, requests, sys, io

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
            print(f"  [{i}] MODE: {edata.get('selected_mode')}")
        elif etype == "tool_call":
            args = edata.get("arguments") or {}
            rt = edata.get("routing_trace") or {}
            llm = rt.get("llm") or {}
            print(f"  [{i}] TOOL: {edata.get('name')} src={edata.get('source')} conf={edata.get('confidence')}")
            print(f"       query: {str(args.get('query',''))[:80]}")
            print(f"       category: {args.get('category')}")
            print(f"       product_ids: {args.get('product_ids')}")
            print(f"       llm_skipped: {rt.get('llm_skipped')} reason={rt.get('llm_skipped_reason')}")
            if llm:
                print(f"       LLM: {llm.get('name')} conf={llm.get('confidence')}")
        elif etype == "delta":
            text = edata.get("text", "")
            if text:
                print(f"  [{i}] DELTA: {text[:130]}")
        elif etype == "product_cards":
            products = edata if isinstance(edata, list) else edata.get("products", [])
            print(f"\n  [{i}] CARDS ({len(products)}):")
            for j, p in enumerate(products[:5]):
                print(f"       [{j+1}] {p.get('brand','')} | {str(p.get('title',''))[:45]} | price={p.get('price','?')}")
        elif etype == "comparison_table":
            rows = edata.get("rows") or []
            print(f"\n  [{i}] COMPARISON_TABLE ({len(rows)} rows):")
            for j, row in enumerate(rows[:5]):
                print(f"       [{j+1}] {row.get('product_id','')} | {row.get('brand','')} | {str(row.get('title',''))[:40]} | price={row.get('price','?')}")
        elif etype == "result":
            req = edata.get("requirement") or {}
            comp = edata.get("comparison") or {}
            comp_rows = comp.get("rows") or []
            cards = edata.get("product_cards") or []
            print(f"\n  [{i}] RESULT: cards={len(cards)} comp_rows={len(comp_rows)}")
            if comp_rows:
                for j, row in enumerate(comp_rows[:5]):
                    print(f"       comp[{j+1}] {row.get('product_id','')} | {str(row.get('title',''))[:40]} | price={row.get('price','?')}")
        elif etype == "done":
            print(f"  [{i}] DONE")
    return events

SID = "trace_case12_v4"
trace_turn(SID, "我想配一台电脑，主要用来做视频剪辑，预算12000。", 1)
trace_turn(SID, "需要NVIDIA的显卡，内存32G以上。", 2)
trace_turn(SID, "机箱要白色的，好看一点。", 3)
trace_turn(SID, "我平时也会玩一些3A游戏。", 4)
trace_turn(SID, "你推荐的两款主板有什么区别？", 5)
trace_turn(SID, "那选第二套吧，帮我看看电源够不够。", 6)

print("\n\nTRACE COMPLETE")
