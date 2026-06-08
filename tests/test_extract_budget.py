"""Quick test for extract_budget fix."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from rag.recommendation.tool_router import extract_budget

tests = [
    ("3000到5000之间的手机", 5000.0, "区间型: 应取上限"),
    ("推荐耳机，不要华为的，500到2000之间", 2000.0, "区间型: 应取上限"),
    ("500元以下的商品有哪些", 500.0, "后缀型"),
    ("预算3000以内", 3000.0, "前缀型"),
    ("不超过5000", 5000.0, "上限关键词"),
    ("5000左右的耳机", 5000.0, "左右型"),
    ("3000以上的手机", None, "以上型: 是下界,不应作为budget"),
    ("预算3000-5000的手机", 5000.0, "区间型"),
    ("手机+耳机，总共不超过1万", 10000.0, "上限+万"),
    ("高端护肤品送妈妈，预算3000以内", 3000.0, "上限型"),
    ("你好", None, "无预算"),
    ("价格在200~800之间", 800.0, "区间型~"),
]

all_ok = True
for text, expected, note in tests:
    got = extract_budget(text)
    ok = got == expected
    if not ok:
        all_ok = False
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {text:40s} expected={expected!s:>10s} got={got!s:>10s} | {note}")

print(f"\n{'ALL PASSED' if all_ok else 'SOME FAILED'}")
