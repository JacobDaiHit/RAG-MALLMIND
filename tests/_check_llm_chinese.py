"""Quick test: which LLM provider handles Chinese correctly?"""
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
        print(f"{p['name']}: SKIP (no key)")
        continue

    data = json.dumps({
        "model": p["model"],
        "messages": [
            {"role": "system", "content": "你是电商路由。输出纯JSON，不要markdown代码块。格式：{\"name\":\"recommend_shopping_products\",\"arguments\":{\"query\":\"推荐一款面霜\",\"category\":\"beauty\"}}"},
            {"role": "user", "content": "推荐一款面霜"},
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
            content = r['choices'][0]['message'].get('content', '')
            # Check if content contains valid Chinese
            has_chinese = any('一' <= c <= '鿿' for c in content)
            print(f"{p['name']}: has_chinese={has_chinese} content={repr(content[:200])}")
            tokens = r.get('usage', {})
            print(f"  tokens: completion={tokens.get('completion_tokens')} total={tokens.get('total_tokens')}")
    except Exception as e:
        print(f"{p['name']}: ERROR {e}")
