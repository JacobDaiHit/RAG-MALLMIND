"""Check LLM response structure for Chinese support."""
import json, urllib.request, os, sys
sys.stdout.reconfigure(encoding='utf-8')

from dotenv import load_dotenv
load_dotenv()

providers = [
    {
        "name": "mimo-v2.5",
        "url": "https://token-plan-cn.xiaomimimo.com/v1/chat/completions",
        "key": os.getenv("MIMO_API_KEY", ""),
        "model": "mimo-v2.5",
    },
    {
        "name": "sensenova-deepseek",
        "url": "https://token.sensenova.cn/v1/chat/completions",
        "key": os.getenv("DEEPSEEK_API_KEY", ""),
        "model": "sensenova-6.7-flash-lite",
    },
]

for p in providers:
    if not p["key"]:
        print(f"\n{p['name']}: SKIP (no key)")
        continue

    print(f"\n{'='*50}")
    print(f"Testing: {p['name']}")

    # Simple prompt to minimize reasoning
    data = json.dumps({
        "model": p["model"],
        "messages": [
            {"role": "user", "content": "只说这两个词：面霜 beauty"},
        ],
        "max_tokens": 100,
        "temperature": 0,
    }, ensure_ascii=False).encode('utf-8')

    req = urllib.request.Request(p["url"], data=data, headers={
        "Authorization": f"Bearer {p['key']}",
        "Content-Type": "application/json; charset=utf-8",
    }, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode('utf-8', errors='replace')
            r = json.loads(raw)
            choice = r['choices'][0]
            msg = choice['message']
            # Check both possible content locations
            content = msg.get('content', '')
            reasoning = msg.get('reasoning_content', '') or msg.get('reasoning', '')
            print(f"finish: {choice.get('finish_reason')}")
            print(f"content ({len(content)} chars): {repr(content[:300])}")
            if reasoning:
                print(f"reasoning: {repr(reasoning[:200])}")
            usage = r.get('usage', {})
            details = usage.get('completion_tokens_details', {})
            print(f"tokens: completion={usage.get('completion_tokens')} reasoning={details.get('reasoning_tokens', 0)}")
    except Exception as e:
        print(f"ERROR: {e}")
