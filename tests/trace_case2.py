"""Case 2 深度追踪：跑步耳机（2轮）—— 为什么第2轮混入小米手机？"""
import json, requests, sys, io, time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:8000/api/chat/stream"
SID = "trace_case2"

def send_and_trace(session_id, message, turn_num):
    """发送请求并完整记录所有 SSE 事件"""
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

    events = send_and_trace(session_id, message, turn_num)

    for i, evt in enumerate(events):
        etype = evt["event"]
        edata = evt["data"]

        if etype == "runtime_mode":
            print(f"\n  [{i}] RUNTIME_MODE:")
            print(f"      selected_mode: {edata.get('selected_mode')}")
            print(f"      reason: {edata.get('reason')}")

        elif etype == "tool_call":
            print(f"\n  [{i}] TOOL_CALL:")
            print(f"      name: {edata.get('name')}")
            print(f"      source: {edata.get('source')}")
            print(f"      confidence: {edata.get('confidence')}")
            args = edata.get("arguments") or {}
            print(f"      arguments:")
            for k, v in args.items():
                if v and k not in ("routing_trace",):
                    print(f"        {k}: {json.dumps(v, ensure_ascii=False)[:100]}")

            # 关键：routing_trace 详情
            rt = edata.get("routing_trace") or {}
            print(f"      routing_trace:")
            print(f"        runtime_mode: {rt.get('runtime_mode')}")
            print(f"        llm_skipped: {rt.get('llm_skipped')}")
            print(f"        llm_skipped_reason: {rt.get('llm_skipped_reason')}")
            print(f"        router_attempted: {rt.get('router_attempted')}")
            print(f"        router_success: {rt.get('router_success')}")
            print(f"        router_applied: {rt.get('router_applied')}")
            print(f"        router_final_source: {rt.get('router_final_source')}")

            local = rt.get("local") or {}
            if isinstance(local, dict):
                print(f"        local.name: {local.get('name')}")
                print(f"        local.confidence: {local.get('confidence')}")
                largs = local.get("arguments") or {}
                print(f"        local.arguments.category: {largs.get('category')}")
                print(f"        local.arguments.query: {str(largs.get('query',''))[:60]}")

            llm = rt.get("llm")
            if llm and isinstance(llm, dict):
                print(f"        llm.name: {llm.get('name')}")
                print(f"        llm.confidence: {llm.get('confidence')}")
                largs = llm.get("arguments") or {}
                print(f"        llm.arguments.category: {largs.get('category')}")
                print(f"        llm.arguments.query: {str(largs.get('query',''))[:60]}")
            else:
                print(f"        llm: None")

            final = rt.get("final") or {}
            if isinstance(final, dict):
                print(f"        final.name: {final.get('name')}")
                print(f"        final.source: {final.get('source')}")

            print(f"        guard_overridden: {rt.get('guard_overridden')}")

        elif etype == "progress":
            label = edata.get("label", "")
            detail = edata.get("detail", "")[:80]
            print(f"  [{i}] PROGRESS: {label} | {detail}")

        elif etype == "intent_route":
            print(f"\n  [{i}] INTENT_ROUTE:")
            print(f"      route: {edata.get('route')}")
            print(f"      task_type: {edata.get('task_type')}")
            print(f"      needs_clarification: {edata.get('needs_clarification')}")

        elif etype == "delta":
            text = edata.get("text", "")
            if text:
                print(f"  [{i}] DELTA: {text[:100]}")

        elif etype == "product_cards":
            products = edata if isinstance(edata, list) else edata.get("products", [])
            print(f"\n  [{i}] PRODUCT_CARDS ({len(products)} products):")
            for j, p in enumerate(products[:6]):
                title = str(p.get("title", "?"))[:45]
                brand = str(p.get("brand", "?"))
                price = p.get("price", p.get("min_price", "?"))
                pid = p.get("product_id", "?")
                score = p.get("score", "?")
                print(f"      [{j+1}] {pid} | {brand} | {title} | price={price} | score={score}")

        elif etype == "candidate_scope":
            print(f"\n  [{i}] CANDIDATE_SCOPE:")
            filters = edata.get("active_filters") or {}
            print(f"      categories: {filters.get('categories')}")
            print(f"      price_max: {filters.get('price_max')}")
            print(f"      brands: {filters.get('brands')}")
            print(f"      excluded_brands: {filters.get('excluded_brands')}")
            print(f"      must_have_terms: {filters.get('must_have_terms')}")

            by_cat = edata.get("by_category") or {}
            for cat, info in by_cat.items():
                if isinstance(info, dict):
                    print(f"      category[{cat}]: raw={info.get('raw_count')} after_exclusion={info.get('after_exclusion_count')} within_budget={info.get('within_budget_count')}")

        elif etype == "result":
            print(f"\n  [{i}] RESULT:")
            req = edata.get("requirement") or {}
            print(f"      raw_query: {req.get('raw_query', '')[:60]}")
            print(f"      desired_categories: {req.get('desired_categories')}")
            print(f"      target_sub_categories: {req.get('target_sub_categories')}")
            print(f"      brands: {req.get('brands')}")
            print(f"      excluded_brands: {req.get('excluded_brands')}")
            print(f"      must_have_terms: {req.get('must_have_terms')}")
            print(f"      price_max: {req.get('price_max')}")
            print(f"      need_bundle: {req.get('need_bundle')}")

            trace = edata.get("trace") or {}
            print(f"      trace.catalog_scope: {trace.get('catalog_scope')}")
            print(f"      trace.recommendation_domain: {trace.get('recommendation_domain')}")
            print(f"      trace.inferred_product_type: {trace.get('inferred_product_type')}")
            print(f"      trace.product_type_filter_applied: {trace.get('product_type_filter_applied')}")

            # 结构化过滤详情
            sf = trace.get("structured_filter") or {}
            for cat, diag in sf.items():
                if isinstance(diag, dict):
                    print(f"      filter[{cat}]: raw={diag.get('raw_count')} stock={diag.get('after_stock_count')} excl={diag.get('after_exclusion_count')} brand={diag.get('after_brand_count')} target={diag.get('after_target_count')} must={diag.get('after_must_have_count')} budget={diag.get('after_budget_count')} returned={diag.get('returned_count')}")
                    if diag.get("product_type_filter_applied"):
                        print(f"        product_type_filter: inferred={diag.get('inferred_product_type')} candidates={diag.get('product_type_candidate_count')}")

        elif etype == "comparison_table":
            rows = edata.get("rows") or []
            if rows:
                print(f"\n  [{i}] COMPARISON_TABLE ({len(rows)} rows):")
                for row in rows[:3]:
                    print(f"      {row.get('product_id')} | {row.get('brand')} | {row.get('title','')[:40]} | price={row.get('price')} | score={row.get('score')}")

        elif etype == "done":
            print(f"\n  [{i}] DONE: session_id={edata.get('session_id')}")

    return events

# ============================================================
# 执行 Case 2
# ============================================================
print("=" * 80)
print("CASE 2: 跑步耳机（2轮）—— 深度链路追踪")
print("=" * 80)

# Turn 1
events1 = trace_turn(SID, "我想买一个跑步用的耳机，有什么推荐？", 1)

print("\n\n" + "#" * 80)
print("# SESSION STATE AFTER TURN 1")
print("#" * 80)

# 查询 session 状态
try:
    r = requests.get(f"http://127.0.0.1:8000/api/session/{SID}", timeout=10)
    if r.status_code == 200:
        session_data = r.json()
        print(f"  Session data: {json.dumps(session_data, ensure_ascii=False, indent=2)[:500]}")
    else:
        print(f"  Session endpoint returned {r.status_code}")
except Exception as e:
    print(f"  Session query failed: {e}")

# Turn 2
events2 = trace_turn(SID, "需要防水，续航要长一点的。", 2)

print("\n\n" + "#" * 80)
print("# SESSION STATE AFTER TURN 2")
print("#" * 80)
try:
    r = requests.get(f"http://127.0.0.1:8000/api/session/{SID}", timeout=10)
    if r.status_code == 200:
        session_data = r.json()
        print(f"  Session data: {json.dumps(session_data, ensure_ascii=False, indent=2)[:500]}")
    else:
        print(f"  Session endpoint returned {r.status_code}")
except Exception as e:
    print(f"  Session query failed: {e}")

print("\n\n" + "=" * 80)
print("TRACE COMPLETE")
print("=" * 80)
