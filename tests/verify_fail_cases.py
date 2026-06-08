"""Verification for FAIL cases — proper SSE parsing."""
import sys, os, io, json, time, requests

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:8000/api/chat/stream"

CASES = [
    (114, "vf_114", "有没有防水的运动手表",
     "期望: 0 cards (missing_subcategory 或 pipeline 兜底)"),
    (120, "vf_120", "有没有好看的裙子",
     "期望: 0 cards (missing_subcategory 或 pipeline 兜底)"),
    (128, "vf_128", "最贵的商品是什么",
     "期望: general_chat"),
    (148, "vf_148", "帮我把 iPhone 17 Pro 加到购物车",
     "期望: recommend + cards (cart_fallback)"),
    (152, "v2_cart", "把华为耳机数量改成2",
     "期望: apply_cart_instruction"),
    (165, "vf_165", "你们有卖 PS5 吗",
     "期望: general_chat"),
    (167, "vf_167", "三星Galaxy S30怎么样",
     "期望: 0 cards (不推荐无关商品)"),
    (168, "vf_168", "有没有一百万以上的商品",
     "期望: 0 cards (不推荐无关商品)"),
    (171, "vf_171", "手机+耳机，总共不超过1万",
     "期望: recommend + cards (budget=10000)"),
]


def send(sid, msg):
    payload = {"message": msg, "session_id": sid, "catalog_scope": "ecommerce"}
    r = requests.post(BASE, json=payload, stream=True, timeout=30)
    r.raise_for_status()
    tool_name, tool_conf, tool_src = "", "", ""
    tool_args = {}
    cards = 0
    reply = ""
    budget_arg = None
    no_match = ""
    intent_route = ""
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
                tool_args = ev.get("arguments") or {}
                budget_arg = tool_args.get("budget")
            elif cur_event == "delta" and "text" in ev:
                reply += ev.get("text", "")
            elif cur_event == "product_card":
                cards += 1
            elif cur_event == "product_cards":
                products = ev.get("products") or []
                cards += len(products)
            elif cur_event == "intent_route":
                intent_route = ev.get("route", "")
                no_match = ev.get("reason", "") or ev.get("no_match_reason", "")
    return {
        "tool": tool_name,
        "conf": tool_conf,
        "src": tool_src,
        "cards": cards,
        "reply": reply[:200],
        "budget": budget_arg,
        "no_match": no_match,
        "intent_route": intent_route,
        "tool_args": tool_args or {},
    }


def judge(cid, res):
    """Evaluate PASS: 核心标准是不展示无关商品（防幻觉）。"""
    tool = res["tool"]
    cards = res["cards"]
    no_match = res.get("no_match", "")
    budget = res.get("budget")

    if cid == 114:
        # 目录无运动手表: 0 cards 即为正确行为
        return cards == 0
    if cid == 120:
        # 目录无裙装: 0 cards 即为正确行为
        return cards == 0
    if cid == 128:
        return tool == "general_chat"
    if cid == 148:
        # cart_fallback: 应走推荐先搜索 iPhone
        return tool == "recommend_shopping_products" and cards > 0
    if cid == 152:
        return tool == "apply_cart_instruction"
    if cid == 165:
        return tool == "general_chat"
    if cid == 167:
        # 目录无三星: 0 cards 即为正确行为
        return cards == 0
    if cid == 168:
        # 无超高价商品: 0 cards 即为正确行为
        return cards == 0
    if cid == 171:
        # budget 正确解析为 10000 + 有推荐结果
        return budget is not None and float(budget) >= 10000 and cards > 0
    return False


def main():
    print("=" * 110)
    print(f"{'#':>5} | {'Tool':<32} | {'Cards':>5} | {'Budget':>8} | {'Judge':<6} | {'Intent':<22} | Reply")
    print("-" * 110)
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
            reply_short = res["reply"][:50].replace("\n", " ")
            budget_str = str(res.get("budget", ""))[:8]
            intent = res.get("intent_route", "") or res.get("no_match", "")
            print(f"{cid:>5} | {res['tool']:<32} | {res['cards']:>5} | {budget_str:>8} | {tag:<6} | {intent:<22} | {reply_short}")
            print(f"      | conf={res['conf']} src={res['src']} {elapsed}ms")
            print(f"      | {expect}")
        except Exception as e:
            print(f"{cid:>5} | ERR: {e}")
        print("-" * 110)

    print(f"\nResult: {passed}/{len(CASES)} PASS")


if __name__ == "__main__":
    main()
