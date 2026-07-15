"""边界测试执行脚本 v2"""
import json, requests, sys, time, io
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:8000/api/chat/stream"

CASES = [
    {"id": 1, "name": "敏感肌面霜选购与对比", "turns": [
        "你好，我最近皮肤有点干，能推荐一款面霜吗？",
        "我是敏感肌，不能有酒精和香精。",
        "价位在300元左右吧，有没有薇诺娜或者理肤泉的？",
        "那薇诺娜的特护霜和理肤泉的B5修复霜哪个更适合我这种全脸干燥的情况？",
        "帮我先把薇诺娜那款加进购物车吧，另外有没有适合敏感肌的洗面奶也推荐一个？",
        "洁面也加进来吧。对了，这个面霜是晚上用的，我白天用有没有推荐的精华，也是干敏皮用的，300以下。",
    ]},
    {"id": 2, "name": "游戏主机装机与配置调整", "turns": [
        "我想装一台能流畅玩《黑神话：悟空》的主机，预算7000左右。",
        "处理器用AMD的吧，感觉性价比高一些。",
        "我看到一个AMD Ryzen 5 5600，这个能省点钱吗？换成这个行不行？",
        "算了，那还是用原来的7500F吧。内存32GB够用吗？要不要上64GB？",
        "那内存还是32GB，帮我把这套配置加到购物车。CPU散热器还没选，加上一个。",
        "电源850W会不会太大了？换成650W的能便宜点吗？",
    ]},
    {"id": 3, "name": "超出产品库的询问与跨品牌对比", "turns": [
        "你们有小米15手机吗？我想买那个。",
        "那小米17 Ultra和iPhone 17 Pro哪个拍照更好？我比较在意夜景和长焦。",
        "iPhone 17 Pro现在多少钱？256GB的。",
        "那华为Pura 90 Pro呢？和iPhone比怎么样？我平时也拍视频。",
        "算了，我还是看看iPhone吧，它支持双卡双待吗？",
    ]},
    {"id": 4, "name": "宽脚跑鞋选购与日常通勤兼顾", "turns": [
        "我想买一双日常慢跑的鞋子，预算1000以内。",
        "我就在小区柏油路跑，每次5公里左右。我脚有点宽，有没有宽楦的？",
        "耐克的Pegasus 41有宽楦版吗？多少钱？",
        "先不加购物车。我除了跑步，平时也想穿着压马路，哪款更百搭好看？",
        "那阿迪达斯的Ultraboost 5怎么样？超预算了，但样子我喜欢，帮我加进去吧。",
    ]},
    {"id": 5, "name": "轻薄本多轮对比与存储扩容", "turns": [
        "我想买个轻薄本，平时办公用，偶尔剪剪短视频，预算8000以内。",
        "华为的MateBook 14和苹果的MacBook Air M5哪个更适合我？我手机是华为的。",
        "那MateBook 14的16GB+1TB版本多少钱？鸿蒙版和锐龙版有什么区别？",
        "我平时存的文件比较多，1TB够用吗？能不能自己加硬盘？",
        "那帮我加一个16GB+1TB鸿蒙版的到购物车吧，再问一下，它送不送手写笔？",
        "不送就算了，我再看看苹果的，MacBook Air M5 13英寸16GB+512GB多少钱？",
    ]},
    {"id": 6, "name": "无糖饮料囤货与临期问题", "turns": [
        "我想买点无糖的茶饮料，整箱的那种，有什么推荐？",
        "东方树叶的乌龙茶和茉莉花茶哪个好喝？我不喜欢太苦的。",
        "那茉莉花茶吧，480ml×15瓶整箱装多少钱？保质期多久？",
        "发货会不会是临期的？我之前买到过剩两三个月的。",
        "好，那帮我下一单，要最新日期的。再问一下，元气森林的白桃味有没有整箱的？",
    ]},
    {"id": 7, "name": "咖啡口味筛选与冷热冲泡", "turns": [
        "我每天早上需要提神，有什么速溶黑咖啡推荐？",
        "我不喜欢酸的，要苦味重一点的，最好能冷水冲泡。",
        "三顿半的数字星球和冷萃款哪个更苦？数字星球6号怎么样？",
        "那冷萃黑咖6颗装多少钱？能直接用冰牛奶冲吗？",
        "帮我加到购物车吧。另外雀巢的金牌冻干和这个比哪个性价比高？",
    ]},
    {"id": 8, "name": "宝宝食品与过敏注意", "turns": [
        "家里有3岁的小朋友，想买点健康的零食，有什么推荐？",
        "不要太多糖和添加剂的，最好是坚果类的。",
        "百草味的每日坚果A款小朋友能吃吗？会不会卡喉咙？",
        "那有没有小颗粒的或者研磨碎的？三只松鼠的那款呢？",
        "算了，我还是买酸奶吧。安慕希的原味酸奶3岁宝宝能喝吗？常温的就行。",
    ]},
    {"id": 9, "name": "摄影爱好者的相机与手机纠结", "turns": [
        "我想买个拍照好点的设备，预算7000左右，手机或者相机都行。",
        "小米17 Ultra和vivo X300 Ultra哪个更出片？我主要拍人像和夜景。",
        "那小米的1TB版本多少钱？16GB+1TB的。",
        "有点贵。那有没有便宜点的相机？比如索尼的微单，你们有吗？",
        "没有啊……那帮我对比一下OPPO Find X9 Ultra和小米17 Ultra的人像模式吧。",
    ]},
    {"id": 10, "name": "厨房调料补货与包装选择", "turns": [
        "家里的生抽快用完了，想买一瓶，要大瓶的，性价比高。",
        "李锦记的草菇老抽和普通生抽有什么区别？老抽能凉拌吗？",
        "那海天的金标生抽呢？1.9L的多少钱？是玻璃瓶还是塑料瓶？",
        "塑料瓶的吧，便宜点。开封后怎么保存不容易坏？我家灶台边挺热的。",
        "那我还是放冰箱吧。帮我把海天金标生抽1.9L塑料装加到购物车。",
    ]},
    {"id": 11, "name": "平板电脑办公与娱乐兼顾", "turns": [
        "我想买个平板，主要用来看网课和记笔记，偶尔也会看电影。",
        "苹果iPad Air 11英寸2026款和华为MatePad Pro 13.2英寸哪个更适合学习？",
        "华为那款12GB+512GB的多少钱？带键盘吗？",
        "键盘套装太贵了，先不加。它的手写笔支持压感吗？记笔记延迟高不高？",
        "那苹果的iPad Air 256GB Wi-Fi版多少钱？深空灰有货吗？",
    ]},
    {"id": 12, "name": "泡面囤货与口味混搭", "turns": [
        "我想囤点方便面，宿舍晚上吃，要桶装的，方便。",
        "康师傅红烧牛肉面和统一老坛酸菜哪个好吃？我口味偏重。",
        "那老坛酸菜的吧，12桶整箱多少钱？会不会碎很多？",
        "如果有碎的怎么办？能赔吗？",
        "行，帮我来一箱。另外有没有日清合味道的海鲜味？可以混着买吗？",
    ]},
    {"id": 13, "name": "耳机选购与运动需求", "turns": [
        "我想买个蓝牙耳机，主要跑步时候戴，要防汗、不容易掉。",
        "苹果的AirPods Pro 3和华为FreeBuds Pro 5哪个更稳？我耳朵眼比较小。",
        "华为的有耳塞套尺寸可以换吗？最小号是多大的？",
        "那它的心率监测功能准不准？跑步时会不会断连？",
        "犹豫……AirPods Pro 3有没有不带心率监测的便宜版本？",
    ]},
    {"id": 14, "name": "电视盒子与影视会员", "turns": [
        "我想买个电视盒子，给家里老人用，操作要简单。",
        "你们有小米电视盒子吗？最新款是什么？",
        "那Apple TV呢？是不是不能用国内的视频App？",
        "算了，太麻烦了。有没有直接能看直播的？比如移动和TV？",
        "看来你们这选择不多，那我再看看吧。",
    ]},
    {"id": 15, "name": "送礼需求与礼盒包装", "turns": [
        "我想买一个送礼的东西，预算200左右，送女生，最好是美妆或者零食。",
        "三只松鼠的每日坚果礼盒怎么样？30袋的那个多少钱？",
        "有没有带提手的礼盒？外观喜庆一点的。",
        "那换成百草味的每日坚果A款40袋家庭分享装，是礼盒包装吗？",
        "好，那就这个。能不能帮忙写个贺卡？或者加个礼品袋？",
    ]},
    {"id": 16, "name": "冰箱食材与临期预警", "turns": [
        "家里鸡蛋快吃完了，有没有新鲜的土鸡蛋？",
        "有机蛋和普通蛋有什么区别？价格差多少？",
        "那算了，买普通蛋。保质期多久？发货是不是新鲜的？",
        "上次买的牛奶就是临期的，这次鸡蛋千万别给我发快过期的。",
        "好，那帮我下一盒30个装的。再问一下，有没有搭配的保鲜盒卖？",
    ]},
    {"id": 17, "name": "手机屏幕维修与售后", "turns": [
        "我手机屏幕碎了，iPhone 17 Pro换屏多少钱？",
        "在你们这买的有保险吗？我没有买AppleCare+。",
        "那我自己找第三方修会影响保修吗？",
        "官方换屏要多久？能不能上门取件？",
        "算了，太麻烦了，我先不换了。你们卖手机壳吗？防摔好点的。",
    ]},
    {"id": 18, "name": "功能饮料提神与健康顾虑", "turns": [
        "经常熬夜，想买点功能饮料，红牛和东鹏哪个提神效果更好？",
        "红牛的24罐整箱多少钱？有没有250ml×12罐的小箱？",
        "这个咖啡因含量高吗？我喝咖啡容易心慌，会不会也有反应？",
        "那有没有不含咖啡因的提神饮料？或者维生素水？",
        "水溶C100算吗？那个是补充维C的，能提神吗？",
    ]},
    {"id": 19, "name": "衣服尺码选择与退换货", "turns": [
        "我想买优衣库的那个AIRism棉质T恤，男款黑色M码有吗？",
        "我身高175，体重70公斤，穿M会不会小？需要L吗？",
        "那宽松版型和常规版型哪个更修身？我比较瘦。",
        "如果买回来不合适能退换吗？运费谁出？",
        "好的，那我先买M码试试。顺便加一条同色的运动短裤，L码的。",
    ]},
    {"id": 20, "name": "跨品类组合购买与优惠计算", "turns": [
        "我购物车里有iPhone 17 Pro、AirPods Pro 3和一瓶海天生抽，能一起结算吗？",
        "有没有满减优惠？比如满10000减多少？",
        "那我把iPhone换成华为Pura 90 Pro呢？价格便宜点，能凑单吗？",
        "还是太贵了。我只要耳机和酱油，再加一箱东方树叶茉莉花茶，运费怎么算？",
        "那帮我算一下总价，我要用信用卡支付，支持分期吗？",
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
    sid = f"bound_v2_{case['id']}"
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
            "args": {k: v for k, v in res.get("args", {}).items() if v and k in ("brands", "exclude_brands", "sort_order", "budget", "category", "sub_category", "price_max", "is_explicit_budget", "action")},
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

output_path = Path(".pytest_tmp") / "bound_test.json"
output_path.parent.mkdir(parents=True, exist_ok=True)
with output_path.open("w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print(f"\nDone. {len(results)} cases saved to {output_path}")
