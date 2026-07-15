"""Convert the canonical bound test JSON to readable markdown."""
import json, sys
sys.stdout.reconfigure(encoding='utf-8')

with open('.pytest_tmp/bound_test.json', encoding='utf-8') as f:
    data = json.load(f)

out_path = '.pytest_tmp/bound_test.md'
with open(out_path, 'w', encoding='utf-8') as out:
    out.write('# 边界测试原始对话记录 v2\n\n')
    out.write('**日期:** 2026-06-11 | **LLM:** mimo-v2.5 / mimo-v2.5-pro\n\n')
    out.write('---\n\n')

    for case in data:
        out.write(f'## Case {case["id"]}: {case["name"]}\n\n')
        for turn in case['turns']:
            i = turn['turn']
            inp = turn['input']
            tool = turn.get('tool', '?')
            src = turn.get('src', '?')
            cards = turn.get('card_count', 0)
            titles = turn.get('card_titles', [])
            reply = turn.get('reply', '')
            args = turn.get('args', {})
            elapsed = turn.get('elapsed_ms', 0)
            error = turn.get('error', '')

            out.write(f'### 第 {i} 轮 [{elapsed}ms]\n\n')
            out.write(f'**用户:** {inp}\n\n')
            out.write(f'**路由:** `{tool}` (source={src})\n\n')
            if args and args != '{}':
                out.write(f'**参数:** {json.dumps(args, ensure_ascii=False)}\n\n')
            if error:
                out.write(f'**错误:** {error}\n\n')
            if cards and titles:
                out.write(f'**商品卡片 ({cards} 张):**\n')
                tl = titles if isinstance(titles, list) else eval(titles)
                for t in tl:
                    out.write(f'- {t}\n')
                out.write('\n')
            if reply:
                out.write(f'**回复:** {reply}\n\n')
            out.write('---\n\n')

        out.write('\n')

print(f'Done: {out_path}')
print(f'Size: {len(open(out_path, encoding="utf-8").read())} chars')
