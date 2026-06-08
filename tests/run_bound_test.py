"""边界测试执行脚本"""
import json, requests, sys, time, io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:8000/api/chat/stream"

CASES = [
    {"id": 1, "name": "面霜推荐", "turns": [
        "你好，我最近皮肤有点干，能推荐一款面霜吗？",
        "我是敏感肌，不能有酒精和香精。",
        "价位在300元左右。",
    ]},
    {"id": 2, "name": "跑步耳机", "turns": [
        "我想买一个跑步用的耳机，有什么推荐？",
        "需要防水，续航要长一点的。",
    ]},
    {"id": 3, "name": "学生轻薄本", "turns": [
        "你们这有适合学生用的轻薄本吗？",
        "预算5000以下。",
    ]},
    {"id": 4, "name": "游戏PC", "turns": [
        "我想配一台能玩黑神话悟空的电脑，预算8000左右。",
        "CPU要Intel的，不要AMD。",
    ]},
    {"id": 5, "name": "0糖饮料", "turns": [
        "有适合夏天喝的0糖饮料吗？",
        "整箱买的话哪种口味比较好喝？",
    ]},
    {"id": 6, "name": "拍照手机", "turns": [
        "我平时喜欢拍风景，哪个手机拍照好？",
        "不要苹果，其他品牌都可以。",
    ]},
    {"id": 7, "name": "速溶咖啡", "turns": [
        "有没有便携的速溶咖啡推荐？",
        "不要三合一，要纯黑咖啡。",
        "最好是冷泡也能溶解的。",
    ]},
    {"id": 8, "name": "散步鞋", "turns": [
        "我想给我爸买个运动鞋，他经常散步。",
        "要大码的，45码左右。",
        "品牌无所谓，舒适就行。",
    ]},
    {"id": 9, "name": "气泡水", "turns": [
        "我最近在减肥，想买个无糖的气泡水。",
        "白桃味的喝腻了，有没有其他口味？",
        "哪个口味评价最好？",
    ]},
    {"id": 10, "name": "双肩包", "turns": [
        "帮我找一个能装下16寸笔记本电脑的双肩包。",
        "要轻便一点，最好有防水功能。",
        "外观不要太花哨，黑色或灰色。",
    ]},
    {"id": 11, "name": "精华液", "turns": [
        "你好，我想买一款精华液，主要想淡斑和提亮肤色。",
        "我是混油皮，不要太油腻的。",
        "预算800元以内。",
        "之前用过科颜氏，效果一般，有其他推荐吗？",
    ]},
    {"id": 12, "name": "视频剪辑PC", "turns": [
        "我想配一台电脑，主要用来做视频剪辑，预算12000。",
        "需要NVIDIA的显卡，内存32G以上。",
        "机箱要白色的，好看一点。",
        "散热用风冷就行，不想要水冷。",
        "我平时也会玩一些3A游戏。",
        "你推荐的两款主板有什么区别？",
        "那选第二套吧，帮我看看电源够不够。",
    ]},
    {"id": 13, "name": "办公降噪耳机", "turns": [
        "我想买个降噪耳机，在办公室用。",
        "预算1500左右。",
        "最好是入耳式的，头戴式太热了。",
        "我手机是华为的，能无缝连接吗？",
        "那华为自己的耳机有哪些型号？",
        "华为FreeBuds Pro 5和4代相比升级大吗？",
        "好，就这个吧。对了，它支持无线充电吗？",
        "我需要单独买充电器吗？",
    ]},
    {"id": 14, "name": "老人牛奶", "turns": [
        "我要给家里老人买牛奶，哪种比较好？",
        "要无糖的，老人血糖有点高。",
        "最好是常温奶，方便储存。",
        "特仑苏和金典哪个更适合？",
        "那有机款和非有机款区别大吗？",
        "好，买一箱24盒的。",
        "顺便帮我加一箱纯甄酸奶，要原味的。",
        "两个一起买有优惠吗？",
    ]},
    {"id": 15, "name": "跑鞋对比", "turns": [
        "我想买一双跑鞋，每天跑5公里左右。",
        "我是正常足弓，体重75公斤。",
        "预算600-1000元。",
        "要缓震好一点的，保护膝盖。",
        "耐克、阿迪、HOKA、特步这几个品牌哪个更适合？",
        "那HOKA Clifton 9和Nike Pegasus 41哪个更软？",
        "我穿42码，你们有货吗？",
        "能不能对比一下这两款的鞋底耐磨性？",
    ]},
    {"id": 16, "name": "绘画平板", "turns": [
        "你好，我想买一台平板电脑，主要用来画画和看网课。",
        "预算4000-6000元。",
        "要支持手写笔，屏幕大一点。",
        "苹果iPad和华为MatePad怎么选？",
        "我平时也用华为手机，是不是生态更好？",
        "那华为MatePad Pro 13.2和12.6除了屏幕还有啥区别？",
        "12.6的版本现在还有货吗？",
        "那个星闪键盘套装版包含手写笔吗？",
        "不包含的话，手写笔单买多少钱？",
        "好，我考虑一下，先不加购物车了。",
    ]},
    {"id": 17, "name": "户外徒步鞋", "turns": [
        "我最近迷上了露营，需要一款户外徒步鞋。",
        "要防水的，Gore-Tex的最好。",
        "预算1000-1500元。",
        "萨洛蒙和迈乐哪个抓地力更好？",
        "我平时也会走一些碎石路和泥地。",
        "那迈乐MOAB 3 GTX和萨洛蒙X ULTRA 4哪个更轻？",
        "42码的迈乐有货吗？",
        "我看评论有人说鞋舌磨脚，是真的吗？",
        "有没有宽楦版本？",
        "那先加购物车，我再看看别的。",
    ]},
    {"id": 18, "name": "眉笔", "turns": [
        "我想给我男朋友买一支眉笔，他眉毛比较淡。",
        "要自然色的，不要黑的。",
        "好上手的那种，他是新手。",
        "花西子和方里哪个更细？",
        "那花西子螺黛生花的经典色号和自然棕哪个适合黑发？",
        "我看有人说容易断，是不是真的？",
        "那有没有推荐的替代品？",
        "算了，还是买花西子吧，加点运费险。",
    ]},
    {"id": 19, "name": "游戏手机长对话", "turns": [
        "我想换一部手机，平时打王者荣耀和原神。",
        "要散热好一点，不发热降频的。",
        "预算5000-7000元。",
        "小米17 Ultra和OPPO Find X9 Ultra哪个游戏表现好？",
        "那屏幕方面，谁的刷新率更高？",
        "电池续航呢？",
        "小米17 Ultra的12+256和16+512差多少钱？",
        "512G版本有现货吗？",
        "你说我买这个还是等双十一？",
        "算了，我要不要考虑一下折叠屏？",
        "折叠屏打游戏手感好吗？",
        "那有什么折叠屏推荐？",
        "小米MIX Fold 5和OPPO Find N6哪个更轻？",
        "折叠屏的内屏容易坏吗？",
        "好，我还是买直板机吧，就小米17 Ultra。",
        "颜色有哪几种？",
        "我要宇宙橙，256G够用吗？",
        "再加一个碎屏险多少钱？",
        "帮我加入购物车，我还要看看别的。",
    ]},
    {"id": 20, "name": "商务笔记本", "turns": [
        "我经常出差，想买一个轻便的笔记本电脑。",
        "要续航长的，至少10小时以上。",
        "主要用于办公和偶尔剪短视频。",
        "预算8000-10000元。",
        "华为MateBook 14和苹果MacBook Air哪个更适合？",
        "我用的是华为手机，是不是选华为更方便？",
        "那华为的鸿蒙版和锐龙版有什么区别？",
        "鸿蒙版能安装Windows软件吗？",
        "不能的话有点麻烦，我有些专业软件只有Win版。",
        "那苹果的MacBook Air M5芯片能装Windows吗？",
        "不能用的话，我还是选联想ThinkBook 14+吧。",
        "联想那个高配版32G+1TB多少钱？",
        "7999元？比官网便宜吗？",
        "有没有送办公软件？",
        "那保修多久？",
        "可以加内存吗？",
        "不能加的话，16G够用吗？",
        "我经常同时开十几个Chrome标签和微信、PPT。",
        "那还是选32G版本吧。",
        "帮我加购物车，我明天再付款。",
    ]},
    {"id": 21, "name": "场景切换", "turns": [
        "我最近打球把手机搞坏了，你们有什么推荐吗？",
        "但是我不想要华为。",
        "我只有4千的预算。",
        "你说我不买手机买智能手表怎么样？",
        "算了，我不要了。给我一个pc装机单吧。",
        "我的装机预算有一万块。",
        "我主要玩各种2a大作。",
        "你推荐的两个装机配置有什么区别？",
        "你帮我把里面的显卡加入购物车吧",
    ]},
]

def send_turn(session_id, message):
    payload = {"message": message, "session_id": session_id, "catalog_scope": "ecommerce"}
    try:
        r = requests.post(BASE, json=payload, stream=True, timeout=120)
        r.raise_for_status()
    except Exception as e:
        return {"error": str(e), "tool": "", "cards": [], "reply": "", "args": {}}

    tool_name, tool_src, tool_args = "", "", {}
    cards = []
    reply = ""

    # 正确解析 SSE：按空行分割事件，合并多行 data 字段
    raw_text = r.content.decode("utf-8", errors="replace")
    events_raw = raw_text.split("\n\n")
    for event_block in events_raw:
        event_type = ""
        data_lines = []
        for line in event_block.split("\n"):
            if line.startswith("event:"):
                event_type = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
            elif data_lines:
                # 多行 data 续行
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
        if event_type == "tool_call" and "name" in ev:
            tool_name = ev.get("name", "")
            tool_src = ev.get("source", "")
            tool_args = ev.get("arguments") or {}
        elif event_type == "delta" and "text" in ev:
            reply += ev.get("text", "")
        elif event_type == "product_cards":
            products = ev if isinstance(ev, list) else ev.get("products", [])
            cards = products
    return {"tool": tool_name, "src": tool_src, "cards": cards, "reply": reply.strip(), "args": tool_args}

results = []
for case in CASES:
    sid = f"bound_{case['id']}"
    case_result = {"id": case["id"], "name": case["name"], "turns": []}
    print(f"\n{'='*60}")
    print(f"Case {case['id']}: {case['name']} ({len(case['turns'])} turns)")
    print(f"{'='*60}")
    for i, msg in enumerate(case["turns"], 1):
        t0 = time.time()
        res = send_turn(sid, msg)
        elapsed = int((time.time() - t0) * 1000)
        turn_data = {
            "turn": i,
            "input": msg,
            "tool": res["tool"],
            "src": res.get("src", ""),
            "card_count": len(res["cards"]),
            "card_titles": [c.get("title", "?")[:35] for c in res["cards"][:4]],
            "reply": res["reply"][:500],
            "args": {k: v for k, v in res.get("args", {}).items() if v and k in ("brands", "exclude_brands", "sort_order", "budget", "category")},
            "elapsed_ms": elapsed,
            "error": res.get("error", ""),
        }
        case_result["turns"].append(turn_data)
        print(f"  [{i}] {msg[:50]}")
        print(f"      tool={res['tool']} src={res.get('src','')} cards={len(res['cards'])} {elapsed}ms")
        if res["cards"]:
            print(f"      products: {turn_data['card_titles']}")
        reply_preview = res["reply"][:150].replace("\n", " ")
        print(f"      reply: {reply_preview}")
    results.append(case_result)

with open("reports/bound_test_raw.json", "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print(f"\nDone. {len(results)} cases saved to reports/bound_test_raw.json")
