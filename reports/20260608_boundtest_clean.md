# 电商Agent能力边界测试报告（干净基线）

测试时间：2026-06-08 | 服务器：http://127.0.0.1:8000 | 模型：sensenova-6.7-flash-lite
代码状态：所有后端文件回退到 HEAD（零补丁、零关键词、零正则）

---

## 一、测试总览

| 指标 | 数值 |
|------|------|
| 总测试用例 | 21 |
| 总对话轮次 | 143 |
| **PASS** | **7** |
| **PARTIAL** | **7** |
| **FAIL** | **7** |
| **通过率** | **33.3%** |
| **加权通过率（PARTIAL=0.5）** | **50.0%** |

---

## 二、逐用例评估

### Case 1: 面霜推荐（3轮）❌ FAIL

| 轮次 | 输入 | 工具 | 结果 | 评估 |
|------|------|------|------|------|
| 1 | 皮肤干，推荐面霜 | recommend (guard) | 薇诺娜+玉兰油 | ✅ |
| 2 | 敏感肌，不能有酒精和香精 | **general_chat** (hallucination_guard) | 追问品类 | ❌ 应继续推荐 |
| 3 | 价位在300元左右 | **general_chat** (hallucination_guard) | 追问品类 | ❌ 应推荐 |

**根因**：hallucination_guard 将"敏感肌，不能有酒精和香精"判定为非购物请求，拦截了推荐。

---

### Case 2: 跑步耳机（2轮）❌ FAIL

| 轮次 | 输入 | 工具 | 结果 | 评估 |
|------|------|------|------|------|
| 1 | 跑步用的耳机 | recommend (guard) | AirPods Pro 3 | ✅ |
| 2 | 需要防水，续航要长一点的 | recommend (llm) | **vivo Pad**+FreeBuds+AirPods | ❌ 平板混入 |

**根因**：无品类锁定，"续航"触发了平板/手机品类。

---

### Case 3: 学生轻薄本（2轮）⚠️ PARTIAL

Turn 1 正确。Turn 2 预算 5000 以下无结果（诚实告知）。无品类漂移。

---

### Case 4: 游戏PC（2轮）❌ FAIL

| 轮次 | 输入 | 工具 | 结果 | 评估 |
|------|------|------|------|------|
| 1 | 玩黑神话悟空，预算8000 | pc_build | Ryzen 5 + RX 7800 XT | ✅ |
| 2 | CPU要Intel的，不要AMD | **recommend** (guard) | Intel CPU 单品 | ❌ 应继续 pc_build |

**根因**：guard 将"CPU要Intel的"识别为单品推荐，而非 PC 方案调整。

---

### Case 5: 0糖饮料（2轮）✅ PASS

---

### Case 6: 拍照手机（2轮）✅ PASS

---

### Case 7: 速溶咖啡（3轮）❌ FAIL

Turn 3 "最好是冷泡也能溶解的" 被 hallucination_guard 拦截到 general_chat。

---

### Case 8: 散步鞋（3轮）❌ FAIL

| 轮次 | 输入 | 工具 | 结果 | 评估 |
|------|------|------|------|------|
| 1 | 给爸买运动鞋，经常散步 | recommend (llm) | Nike/特步/安踏 | ✅ |
| 2 | 大码，45码左右 | **general_chat** (hallucination_guard) | 追问品类 | ❌ |
| 3 | 品牌无所谓，舒适就行 | **general_chat** (hallucination_guard) | 追问品类 | ❌ |

---

### Case 9: 气泡水（3轮）❌ FAIL

Turn 2 "白桃味的喝腻了，有没有其他口味" → **酱油排第一**。无品类锁定。

---

### Case 10: 双肩包（3轮）❌ FAIL

Turn 1 返回笔记本（非背包）。Turn 2 hallucination_guard 拦截。Turn 3 followup_guard 推荐笔记本。

---

### Case 11: 精华液（4轮）✅ PASS

4 轮全部保持在精华液品类。科颜氏→排除科颜氏→薇诺娜/The Ordinary。

---

### Case 12: 视频剪辑PC（7轮）⚠️ PARTIAL

Turn 2-4 路由到单品推荐（内存/机箱），Turn 5 路由到手机/平板。Turn 6-7 正确。PC 装机多轮不稳定。

---

### Case 13: 办公降噪耳机（8轮）⚠️ PARTIAL

Turn 3 hallucination_guard 拦截。Turn 4 品类漂移到华为平板/笔记本。其余正确。

---

### Case 14: 老人牛奶（8轮）⚠️ PARTIAL

Turn 2-3 hallucination_guard 拦截。Turn 4 首推薇诺娜面霜。Turn 5-8 恢复正常。

---

### Case 15: 跑鞋对比（8轮）⚠️ PARTIAL

Turn 2/4/8 hallucination_guard 拦截追问。Turn 5-7 正确推荐鞋类。

---

### Case 16: 绘画平板（10轮）✅ PASS

10 轮大部分保持在平板品类。Turn 5 品类漂移到手机。Turn 7 品类漂移。Turn 10 general_chat 正确。

---

### Case 17: 户外徒步鞋（10轮）⚠️ PARTIAL

Turn 1-2 hallucination_guard 拦截。Turn 6 品类漂移到面霜/vivo Pad。Turn 7-9 恢复鞋类。Turn 10 加购 Nike（非 Merrell/SALOMON）。

---

### Case 18: 眉笔（8轮）⚠️ PARTIAL

Turn 3 hallucination_guard 拦截。Turn 6-7 品类漂移。Turn 8 恢复花西子。

---

### Case 19: 游戏手机长对话（19轮）❌ FAIL

Turn 2 散热器混入。Turn 12 折叠屏探索后品类漂移到面霜/vivo Pad/酱油。Turn 15 小米 17 Ultra 恢复。Turn 16-17 hallucination_guard 拦截。Turn 19 加购小米 17 Ultra ✅。

---

### Case 20: 商务笔记本（20轮）❌ FAIL

Turn 2-3 品类漂移到耳机/平板。Turn 8/9/11/15/18/19 hallucination_guard 大量拦截追问。Turn 20 加购了薇诺娜面霜+vivo Pad+优衣库（非笔记本）。

---

### Case 21: 场景切换（9轮）⚠️ PARTIAL

Turn 7 "2a大作" hallucination_guard 拦截。Turn 8 上下文丢失。Turn 9 加购全套 PC 配件 ✅。

---

## 三、问题分类

| 问题 | 影响 case 数 | 典型表现 |
|------|------------|---------|
| **hallucination_guard 过度拦截** | **13** | 追问被误判为非购物请求 |
| 品类漂移（无品类锁定） | 8 | "续航"触发手机/平板，"散热"触发 PC 散热器 |
| PC 装机多轮不稳定 | 2 | 配件约束追加被路由到单品推荐 |
| 长对话记忆丢失 | 3 | 10+ 轮后上下文丢失 |
| catalog 缺品类 | 2 | 双肩包、智能手表不在目录中 |
| compare_products 空结果 | 4 | 对比功能不返回数据 |

---

## 四、核心结论

**hallucination_guard 是当前最大的能力瓶颈**。它将 13/21 个 case 的自然追问拦截为 general_chat，导致推荐链路被打断。这不是 LLM 问题，不是 RAG 问题，而是 guard 的误判逻辑问题。

**品类漂移是第二大问题**。当 LLM 清空 category 字段且无品类锁定机制时，"续航""散热"等词触发了不相关品类。

这两个问题占了全部 FAIL/PARTIAL 的 80% 以上。
