"""Quick verification for FAIL cases — proper SSE parsing (event: + data: lines)."""
import sys, os, io, json, time, requests

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:8000/api/chat/stream"

CASES = [
    (114, "verify_114", "有没有防水的运动手表",
     "期望: 诚实告知无运动手表 (general_chat 或 missing_subcategory)"),
    (120, "verify_120", "有没有好看的裙子",
     "期望: 诚实告知无裙装 (general_chat 或 missing_subcategory)"),
    (128, "verify_128", "最贵的商品是什么",
     "期望: general_chat"),
    (148, "verify_148", "帮我把 iPhone 17 Pro 加到购物车",
     "期望: recommend_shopping_products + cards"),
    (152, "v2_cart", "把华为耳机数量改成2",
     "期望: apply_cart_instruction"),
    (165, "verify_165", "你们有卖 PS5 吗",
     "期望: general_chat"),
    (167, "verify_167", "三星Galaxy S30怎么样",
     "期望: general_chat 或 missing_subcategory"),
    (168, "verify_168", "有没有一百万以上的商品",
     "期望: general_chat"),
    (171, "verify_171", "手机+耳机，总共不超过1万",
     "期望: recommend + cards (budget=10000)"),
]


def send(sid, msg):
    payload = {"message": msg, "session_id": sid, "catalog_scope": "ecommerce"}
    r = requests.post(BASE, json=payload, stream=True, timeout=30)
    r.raise_for_status()
    tool_name, tool_conf, tool_src = "", "", ""
    cards = 0
    reply = ""
    budget_arg = None
    no_match = ""
    cur_event = ""
    for raw_line in r.iter_lines(decode_unicode=True):
        line = raw_line or ""
        if line.startswith("event:"):
            cur_event = line[6:].strip()
        elif line.startswith("data:"):
            data_str = line[5:].strip()
            if data_str in ("", "[DONE]"):
                continue
            try:
                ev = json.loads(data_str)
            except Exception:
                continue
            if cur_event == "tool_call" and "name" in ev:
                tool_name = ev.get("name", "")
                tool_conf = ev.get("confidence", "")
                tool_src = ev.get("source", "")
                budget_arg = (ev.get("arguments") or {}).get("budget")
            elif cur_event == "delta" and "text" in ev:
                reply += ev.get("text", "")
            elif cur_event == "product_card":
                cards += 1
            elif cur_event == "result":
                ir = ev.get("intent_route") or {}
                no_match = ir.get("no_match_reason", "")
    return {
        "tool": tool_name,
        "conf": tool_conf,
        "src": tool_src,
        "cards": cards,
        "reply": reply[:200],
        "budget": budget_arg,
        "no_match": no_match,
    }


def judge(cid, res):
    tool = res["tool"]
    cards = res["cards"]
    no_match = res.get("no_match", "")
    if cid == 114:
        return tool == "general_chat" or no_match == "missing_subcategory"
    if cid == 120:
        return tool == "general_chat" or no_match == "missing_subcategory"
    if cid == 128:
        return tool == "general_chat"
    if cid == 148:
        return tool == "recommend_shopping_products" and cards > 0
    if cid == 152:
        return tool == "apply_cart_instruction"
    if cid == 165:
        return tool == "general_chat"
    if cid == 167:
        return tool == "general_chat" or no_match == "missing_subcategory"
    if cid == 168:
        return tool == "general_chat"
    if cid == 171:
        return cards > 0 and res.get("budget") is not None and float(res["budget"]) >= 10000
    return False


def main():
    print("=" * 100)
    print(f"{'#':>5} | {'Tool':<32} | {'Cards':>5} | {'Budget':>8} | {'Judge':<8} | Reply (first 70 chars)")
    print("-" * 100)
    passed = 0
    for cid, sid, msg, expect in CASES:
        t0 = time.time()
        try:
            res = send(sid, msg)
            elapsed = int((time.time() - t0) * 1000)
            ok = judge(cid, res)
            tag = "PASS" if ok else "FAIL"
            if ok:
                passed += 1
            reply_short = res["reply"][:70].replace("\n", " ")
            budget_str = str(res.get("budget", ""))[:8]
            print(f"{cid:>5} | {res['tool']:<32} | {res['cards']:>5} | {budget_str:>8} | {tag:<8} | {reply_short}")
            print(f"      | conf={res['conf']} src={res['src']} {elapsed}ms  no_match={res.get('no_match','')}")
            print(f"      | {expect}")
        except Exception as e:
            print(f"{cid:>5} | ERR: {e}")
        print("-" * 100)

    print(f"\nResult: {passed}/{len(CASES)} PASS")


if __name__ == "__main__":
    main()
