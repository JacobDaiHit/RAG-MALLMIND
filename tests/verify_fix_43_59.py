# -*- coding: utf-8 -*-
"""Targeted verification for #43 and #59 fixes."""
import json
import sys
import io
import time
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

BASE = "http://localhost:8000"
CHAT_URL = f"{BASE}/api/chat/stream"

def send_chat(text: str, session_id: str = "") -> list:
    """Send POST /api/chat/stream and parse SSE events."""
    payload = {
        "message": text,
        "session_id": session_id,
        "attachments": [],
        "images": [],
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        CHAT_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    events = []
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            buffer = ""
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace")
                buffer += line
                while "\n\n" in buffer:
                    event_text, buffer = buffer.split("\n\n", 1)
                    ev = parse_sse(event_text)
                    if ev:
                        events.append(ev)
            if buffer.strip():
                ev = parse_sse(buffer)
                if ev:
                    events.append(ev)
    except Exception as e:
        events.append({"_error": str(e)})
    return events

def parse_sse(text: str):
    event_type = ""
    data_lines = []
    for line in text.strip().split("\n"):
        if line.startswith("event:"):
            event_type = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].strip())
    if not data_lines:
        return None
    try:
        obj = json.loads("".join(data_lines))
        obj["_sse_event"] = event_type
        return obj
    except json.JSONDecodeError:
        return {"_sse_event": event_type, "_raw": "".join(data_lines)}

def count_cards(events):
    cards = []
    for ev in events:
        if ev.get("_sse_event") == "product_cards" or ev.get("type") == "product_cards":
            cards.extend(ev.get("products") or [])
    return cards

def get_tool_name(events):
    for ev in events:
        t = ev.get("_sse_event") or ev.get("type") or ""
        if t == "tool_call":
            return ev.get("tool_name") or ev.get("name") or ""
    # Check for tool field in other events
    for ev in events:
        if ev.get("tool_name") or ev.get("tool"):
            return str(ev.get("tool_name") or ev.get("tool"))
    return ""

def get_text_reply(events):
    parts = []
    for ev in events:
        t = ev.get("_sse_event") or ev.get("type") or ""
        if t in ("text_chunk", "text"):
            parts.append(ev.get("text", "") or ev.get("chunk", "") or ev.get("content", ""))
    return "".join(parts)

def dump_events(events, label=""):
    print(f"  [events {label}]:")
    for ev in events:
        t = ev.get("_sse_event") or ev.get("type") or "?"
        keys = [k for k in ev if not k.startswith("_")]
        print(f"    {t}: {keys[:8]}")

# ── Test #43: "不要第一个了" in cart session ──
print("=" * 60)
print("Testing #43: cart followup routing")
print("=" * 60)
cart_session = "sess_verify43"

print("\n[1/3] Adding items to cart...")
r1 = send_chat("把洗面奶加入购物车", session_id=cart_session)
tool1 = get_tool_name(r1)
cards1 = count_cards(r1)
text1 = get_text_reply(r1)
print(f"  Tool: '{tool1}', Cards: {len(cards1)}")
if text1: print(f"  Reply: {text1[:100]}")
dump_events(r1, "step1")
time.sleep(0.5)

print("\n[2/3] Adding another item...")
r2 = send_chat("再把那个精华液也加进去", session_id=cart_session)
tool2 = get_tool_name(r2)
cards2 = count_cards(r2)
text2 = get_text_reply(r2)
print(f"  Tool: '{tool2}', Cards: {len(cards2)}")
if text2: print(f"  Reply: {text2[:100]}")
dump_events(r2, "step2")
time.sleep(0.5)

print("\n[3/3] '不要第一个了' (should route to CART)...")
r3 = send_chat("不要第一个了", session_id=cart_session)
tool3 = get_tool_name(r3)
cards3 = count_cards(r3)
text3 = get_text_reply(r3)
print(f"  Tool: '{tool3}', Cards: {len(cards3)}")
if text3: print(f"  Reply: {text3[:200]}")
dump_events(r3, "step3")

if "cart" in tool3.lower():
    print("\n  >>> PASS #43: correctly routed to cart tool")
elif tool3 == "":
    print(f"\n  >>> WARN #43: no tool_call event found")
else:
    print(f"\n  >>> FAIL #43: expected cart, got '{tool3}'")

# ── Test #59: combined intent ──
print("\n" + "=" * 60)
print("Testing #59: combined intent routing")
print("=" * 60)
combo_session = "sess_verify59"

print("\n[1] Combined intent message...")
r59 = send_chat("推荐手机，直接帮我加到购物车", session_id=combo_session)
tool59 = get_tool_name(r59)
cards59 = count_cards(r59)
text59 = get_text_reply(r59)
print(f"  Tool: '{tool59}', Cards: {len(cards59)}")
if text59: print(f"  Reply: {text59[:200]}")
dump_events(r59, "combo")

if "recommend" in tool59.lower():
    print(f"\n  >>> PASS #59: correctly routed to recommend ({len(cards59)} cards)")
elif tool59 == "":
    print(f"\n  >>> WARN #59: no tool_call event found")
else:
    print(f"\n  >>> FAIL #59: expected recommend, got '{tool59}'")

print("\n" + "=" * 60)
print("Done.")
