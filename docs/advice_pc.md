下面给你一套可以直接用于**装机推荐 RAG 项目**的完整工作流程设计。它把前面提到的 rule、五类方案模板、追问机制、Prompt 重构、方案版本管理、方案对比、数据库字段都整合起来。

你可以把这个流程理解成：

> 用户输入需求 → 系统生成粗方案 → 系统发现信息缺口并追问 → 用户补充 → 系统重构需求 Prompt → 重新检索和生成方案 → 与上一版方案对比 → 输出优劣分析 → 继续迭代。

---

# 一、项目总体目标

你的系统不是简单地回答：

> “8000 元装一台电脑怎么配？”

而是要完成一个**多轮装机推荐流程**：

```text
用户需求
  ↓
需求解析
  ↓
生成粗方案
  ↓
识别缺失信息
  ↓
向用户追问
  ↓
用户补充
  ↓
更新需求画像
  ↓
重新生成 Prompt
  ↓
生成新版方案
  ↓
与旧方案对比
  ↓
输出新版方案 + 优劣分析
  ↓
继续追问或结束推荐
```

核心思想是：

> **结构化规则保证正确性，RAG 检索保证知识来源，LLM 负责理解需求、解释取舍和生成自然语言。**

---

# 二、系统角色划分

建议把系统拆成几个模块，而不是让大模型一次性做完所有事情。

```text
1. 用户需求解析模块
2. 信息完整度检查模块
3. 追问生成模块
4. 推荐模板选择模块
5. 配件检索模块
6. 兼容性校验模块
7. 方案生成模块
8. 方案评分模块
9. 方案版本对比模块
10. Prompt 重构模块
11. 最终解释生成模块
```

---

# 三、用户第一次提出需求时的流程

## 1. 用户输入示例

```text
我想装一台 8000 元左右的电脑，平时打游戏，也做一点 Android 开发。
```

系统不能马上只生成一套固定配置，而要先抽取结构化需求。

---

## 2. 需求解析结果

系统应将用户自然语言转成结构化字段：

```json
{
  "budget": {
    "amount": 8000,
    "currency": "CNY",
    "flexible_range": 500
  },
  "usage": ["gaming", "android_development"],
  "usage_priority": {
    "gaming": 0.6,
    "android_development": 0.4
  },
  "target_resolution": null,
  "target_fps": null,
  "game_type": null,
  "need_ai": false,
  "need_video_editing": false,
  "need_wifi": null,
  "case_size_preference": null,
  "noise_sensitive": null,
  "brand_preference": null,
  "avoid_used_parts": true,
  "existing_parts": [],
  "missing_fields": [
    "target_resolution",
    "target_fps",
    "game_type",
    "need_wifi",
    "case_size_preference"
  ]
}
```

---

# 四、信息完整度判断

不是所有字段都必须追问。你需要区分：

## 必问字段

这些字段会直接影响方案方向。

| 字段        |    是否必问 | 原因        |
| --------- | ------: | --------- |
| 预算        |       是 | 决定硬件档位    |
| 主要用途      |       是 | 决定预算权重    |
| 是否包含显示器   |       是 | 影响整机预算    |
| 是否接受二手    |       是 | 影响候选库     |
| 游戏分辨率     | 游戏用户建议问 | 直接决定显卡档位  |
| AI/本地模型需求 | AI 用户必问 | 决定显存需求    |
| 开发类型      | 开发用户建议问 | 决定内存和 CPU |

## 可选字段

这些字段不一定阻塞粗方案生成。

| 字段         | 作用      |
| ---------- | ------- |
| 机箱大小偏好     | 影响主板和机箱 |
| RGB 偏好     | 影响外观和价格 |
| 噪音敏感度      | 影响散热和机箱 |
| 品牌偏好       | 影响重排    |
| 是否需要 Wi-Fi | 影响主板选择  |
| 是否需要升级空间   | 影响平台选择  |

---

# 五、第一次生成粗方案的规则

用户第一次信息不完整时，系统应该先生成一个**默认粗方案**，并明确说明假设条件。

例如：

```text
在你没有说明显示器分辨率和游戏类型前，我先按：
- 预算 8000 元
- 主机预算，不含显示器
- 游戏 + Android 开发混合用途
- 偏 2K 游戏
- 不买二手
- 需要较好的升级空间

生成一套粗方案。
```

---

## 粗方案示例

```json
{
  "version": 1,
  "plan_type": "rough",
  "assumptions": [
    "预算 8000 元为主机预算，不含显示器",
    "用户主要玩 2K 游戏",
    "Android 开发需要 32GB 以上内存",
    "默认不接受二手配件"
  ],
  "build": {
    "cpu": "Ryzen 5 9600X",
    "motherboard": "B650M DDR5 主板",
    "gpu": "RTX 5070",
    "memory": "DDR5 32GB 6000MHz",
    "storage": "2TB NVMe SSD",
    "psu": "750W 金牌电源",
    "cooler": "双塔风冷",
    "case": "M-ATX 风道机箱"
  },
  "estimated_price": 8000,
  "reason": "该方案优先保证 2K 游戏体验，同时保留 Android 开发所需的 CPU 和内存基础。"
}
```

---

# 六、系统追问逻辑

系统生成粗方案后，不是简单结束，而是继续追问关键问题。

## 追问原则

一次不要问太多。建议每轮最多问 3 个问题。

系统要优先问**会显著改变配置的问题**。

---

## 追问优先级

### 游戏用户

优先问：

```text
1. 你主要玩什么游戏？
2. 显示器是 1080p、2K 还是 4K？
3. 目标帧率是 60、144 还是更高？
```

### 开发用户

优先问：

```text
1. 是否经常开 Android 模拟器？
2. 是否需要 Docker、虚拟机、本地数据库？
3. 是否希望内存上 64GB？
```

### AI 用户

优先问：

```text
1. 是否要本地跑 Stable Diffusion 或大模型？
2. 模型规模大概多大？
3. 是否必须 NVIDIA 显卡？
```

### 外观/体积用户

优先问：

```text
1. 想要小机箱还是普通机箱？
2. 是否在意噪音？
3. 是否要 RGB？
```

---

## 示例追问

```text
这套方案是按 2K 游戏 + Android 开发的均衡配置生成的。

为了进一步优化，我需要确认三个问题：

1. 你的预算 8000 元是否只包含主机，不包含显示器、键鼠？
2. 你主要玩 1080p、2K 还是 4K？
3. Android 开发时是否经常同时开模拟器、Docker、数据库？
```

---

# 七、用户补充信息后的处理流程

假设用户回答：

```text
预算只包含主机。我主要玩 2K，目标 144Hz。开发时会开 Android Studio、模拟器、Docker 和 MySQL。
```

系统应更新需求画像。

---

## 更新后的用户画像

```json
{
  "budget": {
    "amount": 8000,
    "currency": "CNY",
    "includes_monitor": false
  },
  "usage": ["gaming", "android_development"],
  "usage_priority": {
    "gaming": 0.55,
    "android_development": 0.45
  },
  "target_resolution": "1440p",
  "target_fps": 144,
  "game_type": "mixed",
  "development_tools": [
    "Android Studio",
    "Android Emulator",
    "Docker",
    "MySQL"
  ],
  "memory_recommended": "64GB",
  "need_wifi": null,
  "avoid_used_parts": true
}
```

这个时候，系统要生成一个新的 Prompt，交给 RAG + 规则引擎生成新版方案。

---

# 八、新 Prompt 生成规则

用户每次补充信息后，都不要直接把用户原话丢给模型，而是生成一个结构化 Prompt。

---

## Prompt 模板

```text
你是一个装机推荐系统。

请基于以下用户需求生成一套电脑主机方案：

【预算】
{budget_amount} {currency}
是否包含显示器：{includes_monitor}

【主要用途】
{usage}
用途权重：{usage_priority}

【游戏需求】
分辨率：{target_resolution}
目标帧率：{target_fps}
游戏类型：{game_type}

【开发需求】
开发工具：{development_tools}
是否需要虚拟机/Docker/模拟器：{need_virtualization}

【用户偏好】
是否接受二手：{avoid_used_parts}
是否需要 Wi-Fi：{need_wifi}
机箱偏好：{case_size_preference}
噪音敏感：{noise_sensitive}

【规则要求】
1. 必须通过 CPU、主板、内存、显卡、机箱、电源、散热兼容性检查。
2. 游戏用途优先保证显卡性能。
3. Android 开发 + Docker + 模拟器场景建议 64GB 内存。
4. 不允许使用杂牌电源。
5. 方案总价应尽量接近预算，但允许上下浮动 {flexible_range}。
6. 输出新版方案，并与上一版方案进行对比。

【上一版方案】
{previous_build}

请输出：
1. 新方案配置表
2. 与上一版方案的差异
3. 新方案优点
4. 新方案缺点
5. 适合用户的原因
6. 是否还需要继续追问
```

---

# 九、新方案生成逻辑

因为用户补充了“Android Studio + 模拟器 + Docker + MySQL”，系统应该把内存从 32GB 提高到 64GB。

但预算不变，所以需要在其他地方取舍。

---

## 上一版方案

```text
Ryzen 5 9600X
B650M
RTX 5070
32GB DDR5
2TB SSD
750W 电源
双塔风冷
M-ATX 机箱
```

## 新版方案

```text
Ryzen 7 7700 / Ryzen 7 9700X
B650M
RTX 5060 Ti / RTX 5070
64GB DDR5
2TB SSD
750W 金牌电源
双塔风冷
M-ATX 风道机箱
```

这里有一个推荐策略变化：

```text
旧方案：偏游戏
新方案：游戏 + 开发更均衡
```

---

# 十、每次生成新方案都要和旧方案对比

这是你项目里的关键点。

不要只输出新方案，要输出：

```text
方案 A 相比方案 B 改了什么？
为什么要改？
改完后谁受益？
牺牲了什么？
```

---

## 对比字段

建议每套方案都记录以下字段，方便对比：

```json
{
  "build_version": 2,
  "cpu_score": {
    "gaming": 8.2,
    "productivity": 8.7,
    "multi_task": 8.8
  },
  "gpu_score": {
    "1080p": 9.0,
    "1440p": 8.5,
    "4k": 6.8,
    "ai": 7.5
  },
  "memory_score": {
    "capacity": 9.5,
    "development": 9.5
  },
  "storage_score": 8.5,
  "compatibility_score": 10,
  "budget_fit_score": 8.8,
  "upgrade_score": 8.5,
  "reliability_score": 8.8,
  "overall_score": 8.9
}
```

---

## 对比输出示例

| 对比项  | 旧方案 V1        | 新方案 V2                 | 变化                     |
| ---- | ------------- | ---------------------- | ---------------------- |
| CPU  | Ryzen 5 9600X | Ryzen 7 7700 / 9700X   | 多核能力更强                 |
| 显卡   | RTX 5070      | RTX 5060 Ti / RTX 5070 | 可能略降显卡预算               |
| 内存   | 32GB          | 64GB                   | 明显更适合 Android + Docker |
| SSD  | 2TB           | 2TB                    | 不变                     |
| 游戏表现 | 更强            | 略弱或接近                  | 看显卡是否降级                |
| 开发表现 | 中等偏上          | 明显更好                   | 多任务更稳                  |
| 预算压力 | 较均衡           | 更紧                     | 需要压缩显卡或 CPU 预算         |

---

## 新方案优点

```text
1. 64GB 内存更适合 Android Studio、模拟器、Docker、数据库同时运行。
2. CPU 从 6 核级别提升到 8 核级别后，多任务和编译体验更好。
3. 2TB SSD 保留，适合项目、镜像、游戏共存。
4. AM5 平台后续仍有升级空间。
```

## 新方案缺点

```text
1. 如果为了控制 8000 元预算而把 RTX 5070 降到 RTX 5060 Ti，2K 高刷游戏性能会下降。
2. 64GB 内存会挤压显卡预算。
3. 如果用户游戏优先级高于开发，旧方案可能更适合。
```

## 系统结论

```text
如果你更看重 2K 144Hz 游戏，保留 V1。
如果你更看重 Android 开发、多任务、模拟器和 Docker，选择 V2。
如果你想两边都兼顾，可以保留 RTX 5070，但预算可能需要上调到 8500-9000 元。
```

---

# 十一、方案版本管理设计

你需要把每一次方案都保存下来。

不要覆盖旧方案。

---

## build_versions 表

```sql
CREATE TABLE build_versions (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    session_id BIGINT NOT NULL,
    version_number INT NOT NULL,

    plan_type VARCHAR(50), -- rough / refined / final
    title VARCHAR(255),

    total_price DECIMAL(10,2),
    currency VARCHAR(20),

    usage_summary TEXT,
    assumptions JSON,
    user_constraints JSON,

    cpu_id BIGINT,
    motherboard_id BIGINT,
    gpu_id BIGINT,
    memory_id BIGINT,
    storage_id BIGINT,
    psu_id BIGINT,
    cooler_id BIGINT,
    case_id BIGINT,

    compatibility_score DECIMAL(5,2),
    performance_score DECIMAL(5,2),
    budget_fit_score DECIMAL(5,2),
    reliability_score DECIMAL(5,2),
    upgrade_score DECIMAL(5,2),
    overall_score DECIMAL(5,2),

    reason TEXT,
    weakness TEXT,

    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

---

# 十二、用户需求画像表设计

## user_requirement_profiles 表

```sql
CREATE TABLE user_requirement_profiles (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    session_id BIGINT NOT NULL,

    budget_amount DECIMAL(10,2),
    currency VARCHAR(20),
    budget_flexible_range DECIMAL(10,2),
    includes_monitor BOOLEAN,
    includes_keyboard_mouse BOOLEAN,

    usage_types JSON,
    usage_priority JSON,

    target_resolution VARCHAR(50),
    target_fps INT,
    game_types JSON,
    game_titles JSON,

    development_tools JSON,
    need_android_emulator BOOLEAN,
    need_docker BOOLEAN,
    need_virtual_machine BOOLEAN,
    need_database BOOLEAN,

    need_ai BOOLEAN,
    ai_tasks JSON,
    min_vram_gb INT,

    need_video_editing BOOLEAN,
    video_editing_software JSON,

    need_wifi BOOLEAN,
    need_bluetooth BOOLEAN,

    case_size_preference VARCHAR(50),
    noise_sensitive BOOLEAN,
    rgb_preference VARCHAR(50),

    brand_preference JSON,
    brand_blacklist JSON,

    accept_used_parts BOOLEAN,
    existing_parts JSON,

    missing_fields JSON,
    confidence_score DECIMAL(5,2),

    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

---

# 十三、对话会话表设计

## recommendation_sessions 表

```sql
CREATE TABLE recommendation_sessions (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    user_id BIGINT,

    status VARCHAR(50), 
    -- collecting_requirements / rough_plan_generated / refining / final_confirmed

    current_version_id BIGINT,
    final_version_id BIGINT,

    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

---

# 十四、用户消息与系统追问表

## conversation_messages 表

```sql
CREATE TABLE conversation_messages (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    session_id BIGINT NOT NULL,

    role VARCHAR(50), 
    -- user / assistant / system

    message_text TEXT,
    extracted_data JSON,

    related_build_version_id BIGINT,

    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

---

## clarification_questions 表

```sql
CREATE TABLE clarification_questions (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    session_id BIGINT NOT NULL,

    question_text TEXT,
    question_type VARCHAR(100),
    priority INT,

    target_field VARCHAR(100),
    is_answered BOOLEAN DEFAULT FALSE,
    user_answer TEXT,

    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    answered_at DATETIME
);
```

字段示例：

```json
{
  "question_type": "gaming_resolution",
  "target_field": "target_resolution",
  "priority": 1
}
```

---

# 十五、方案对比表设计

每次生成新版方案，都应该和上一版方案做一次对比。

## build_comparisons 表

```sql
CREATE TABLE build_comparisons (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,

    session_id BIGINT NOT NULL,
    old_version_id BIGINT NOT NULL,
    new_version_id BIGINT NOT NULL,

    changed_parts JSON,
    unchanged_parts JSON,

    old_total_price DECIMAL(10,2),
    new_total_price DECIMAL(10,2),
    price_difference DECIMAL(10,2),

    old_score JSON,
    new_score JSON,

    new_plan_advantages TEXT,
    new_plan_disadvantages TEXT,
    old_plan_advantages TEXT,
    old_plan_disadvantages TEXT,

    recommendation_summary TEXT,

    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

---

## changed_parts 示例

```json
{
  "cpu": {
    "old": "Ryzen 5 9600X",
    "new": "Ryzen 7 7700",
    "reason": "用户补充需要 Android 模拟器、Docker 和数据库，多核性能需求上升"
  },
  "memory": {
    "old": "32GB DDR5",
    "new": "64GB DDR5",
    "reason": "开发多任务需求明显增加"
  },
  "gpu": {
    "old": "RTX 5070",
    "new": "RTX 5060 Ti",
    "reason": "为了控制预算，将部分显卡预算转移给内存和 CPU"
  }
}
```

---

# 十六、配件 JSON 商品卡字段设计

这是项目最关键的数据基础。原型阶段不使用 SQL 数据库，而是使用 `data/parts.json` 作为轻量级商品库。

设计原则：

```text
1. 一个 JSON 文件 = 一个商品库
2. 一个 JSON 对象 = 一张完整商品卡
3. part_type 区分 CPU、主板、显卡、内存、硬盘、电源、散热器、机箱
4. specs 放硬件规格
5. scores 放性能评分
6. compatibility 放兼容性约束
7. source 放来源、图片、原始文本、更新时间
```

推荐文件结构：

```text
data/
  parts.json
```

整体格式：

```json
[
  {
    "part_id": "cpu_amd_ryzen_5_9600x",
    "part_type": "cpu",
    "brand": "AMD",
    "model": "Ryzen 5 9600X",
    "title": "AMD Ryzen 5 9600X 处理器",
    "price": 1599.0,
    "currency": "CNY",
    "is_available": true,
    "specs": {},
    "scores": {},
    "compatibility": {},
    "tags": [],
    "selling_points": [],
    "limitations": [],
    "recommendation_text": "",
    "source": {
      "platform": "JD",
      "sku_id": null,
      "source_url": null,
      "raw_specs_text": null,
      "updated_at": "2026-05-25 12:00:00"
    }
  }
]
```

---

## 1. CPU 商品卡 JSON

```json
{
  "part_id": "cpu_amd_ryzen_5_9600x",
  "part_type": "cpu",
  "brand": "AMD",
  "model": "Ryzen 5 9600X",
  "series": "Ryzen 9000",
  "title": "AMD Ryzen 5 9600X 处理器",

  "price": 1599.0,
  "currency": "CNY",
  "is_available": true,

  "specs": {
    "socket": "AM5",
    "core_count": 6,
    "thread_count": 12,
    "base_clock_ghz": 3.9,
    "boost_clock_ghz": 5.4,
    "tdp_w": 65,
    "has_integrated_gpu": true,
    "architecture": "Zen 5",
    "platform": "AMD AM5",
    "release_year": 2024
  },

  "scores": {
    "gaming": 86.0,
    "productivity": 78.0,
    "compile": 75.0,
    "multitask": 72.0
  },

  "compatibility": {
    "requires_motherboard_socket": "AM5",
    "requires_memory_type": "DDR5",
    "requires_cooler_socket": "AM5",
    "recommended_cooling_capacity_w": 120
  },

  "tags": ["AM5", "DDR5", "6核12线程", "游戏CPU", "中高端装机"],

  "selling_points": [
    "单核性能强，适合游戏场景",
    "AM5 平台后续升级空间较好",
    "功耗较低，对散热和电源压力不大"
  ],

  "limitations": [
    "多核生产力不如 8 核或 12 核 CPU",
    "需要搭配 AM5 主板和 DDR5 内存"
  ],

  "recommendation_text": "适合游戏为主、兼顾轻中度开发和日常生产力的装机方案。",

  "source": {
    "platform": "JD",
    "sku_id": null,
    "source_url": null,
    "raw_specs_text": null,
    "updated_at": "2026-05-25 12:00:00"
  }
}
```

重点字段：

```text
specs.socket
specs.core_count
specs.thread_count
specs.tdp_w
scores.gaming
scores.compile
price
```

---

## 2. 主板商品卡 JSON

```json
{
  "part_id": "motherboard_msi_b650m_mortar_wifi",
  "part_type": "motherboard",
  "brand": "MSI",
  "model": "B650M MORTAR WIFI",
  "title": "微星 B650M MORTAR WIFI 主板",

  "price": 1299.0,
  "currency": "CNY",
  "is_available": true,

  "specs": {
    "socket": "AM5",
    "chipset": "B650",
    "form_factor": "M-ATX",
    "memory_type": "DDR5",
    "memory_slots": 4,
    "max_memory_gb": 192,
    "max_memory_speed_mhz": 7600,
    "pcie_x16_slots": 1,
    "m2_slots": 2,
    "sata_ports": 4,
    "has_wifi": true,
    "has_bluetooth": true,
    "usb_c_front_header": true,
    "usb_c_rear": true,
    "supported_cpu_series": ["Ryzen 7000", "Ryzen 8000", "Ryzen 9000"],
    "bios_version_required": null
  },

  "scores": {
    "vrm_quality": 8.5,
    "expansion": 8.0,
    "connectivity": 8.5,
    "upgrade_potential": 8.0
  },

  "compatibility": {
    "supports_cpu_socket": "AM5",
    "requires_memory_type": "DDR5",
    "case_form_factor_required": "M-ATX",
    "supports_pcie_gpu": true
  },

  "tags": ["AM5", "B650", "M-ATX", "DDR5", "Wi-Fi", "前置 Type-C"],

  "selling_points": [
    "支持 AM5 平台和 DDR5 内存",
    "带 Wi-Fi 和蓝牙，减少额外网卡需求",
    "M-ATX 规格适合多数中塔和小型机箱"
  ],

  "limitations": [
    "扩展能力弱于 ATX 主板",
    "部分新 CPU 可能需要确认 BIOS 版本"
  ],

  "recommendation_text": "适合 AM5 中高端装机，兼顾扩展、无线连接和预算控制。",

  "source": {
    "platform": "JD",
    "sku_id": null,
    "source_url": null,
    "raw_specs_text": null,
    "updated_at": "2026-05-25 12:00:00"
  }
}
```

重点字段：

```text
specs.socket
specs.chipset
specs.form_factor
specs.memory_type
specs.memory_slots
specs.max_memory_gb
specs.has_wifi
specs.supported_cpu_series
```

---

## 3. 显卡商品卡 JSON

```json
{
  "part_id": "gpu_jd_100356288414",
  "part_type": "gpu",
  "platform": "JD",
  "sku_id": "100356288414",
  "brand": "瀚铠（VASTARMOR）",
  "model": "RX 9070 16GB OC 超合金 PRO 白色显卡",
  "title": "瀚铠 RX 9070 16GB OC 超合金 PRO 白色显卡",

  "price": 4899.0,
  "currency": "CNY",
  "is_available": true,

  "specs": {
    "chip_vendor": "AMD",
    "gpu_chip": "RX 9070",
    "vram_gb": 16,
    "vram_type": "GDDR6",
    "memory_bus_bit": 256,
    "stream_processors": 3584,
    "tdp_w": null,
    "recommended_psu_w": 650,
    "power_connector": "8pin x 2",
    "length_mm": 332,
    "slot_width": null,
    "cooling_fans": "三风扇",
    "rgb": "ARGB",
    "cuda_support": false,
    "tensor_core_generation": null,
    "nvenc_generation": null,
    "release_year": null
  },

  "scores": {
    "gaming_1080p": null,
    "gaming_1440p": null,
    "gaming_4k": null,
    "ai": 35.0,
    "rendering": null,
    "video_editing": null
  },

  "compatibility": {
    "requires_psu_w": 650,
    "requires_case_gpu_length_mm": 332,
    "requires_power_connector": "8pin x 2",
    "cuda_required_workloads": false
  },

  "tags": ["AMD显卡", "RX 9070", "16GB显存", "GDDR6", "2K游戏", "白色显卡", "非CUDA"],

  "selling_points": [
    "16GB GDDR6 显存，适合 2K 游戏和较高纹理负载场景",
    "三风扇散热设计，适合中高端游戏主机",
    "白色外观，适合白色主题装机"
  ],

  "limitations": [
    "不支持 CUDA，不适合依赖 CUDA 生态的 AI 训练任务",
    "显卡长度约 332mm，购买前需要确认机箱兼容性",
    "建议搭配 650W 以上电源"
  ],

  "recommendation_text": "适合预算中高、主要玩 2K 游戏、想要白色主题装机的用户；不适合作为 CUDA AI 训练卡。",

  "source": {
    "platform": "JD",
    "sku_id": "100356288414",
    "source_url": "https://item.jd.com/100356288414.html",
    "raw_specs_text": "品牌 瀚铠（VASTARMOR）；显卡型号 RX 9070；显存容量 16GB；显存类型 GDDR6；显存位宽 256bit；建议电源 650W以上；电源接口 8pin x 2；显卡长度 33.2cm；散热风扇 三风扇；灯效 ARGB；芯片组 AMD芯片。",
    "updated_at": "2026-05-25 12:00:00"
  }
}
```

重点字段：

```text
specs.vram_gb
specs.tdp_w
specs.recommended_psu_w
specs.length_mm
specs.cuda_support
scores.gaming_1440p
scores.ai
price
```

---

## 4. 内存商品卡 JSON

```json
{
  "part_id": "memory_kingston_fury_ddr5_32g_6000",
  "part_type": "memory",
  "brand": "Kingston",
  "model": "FURY Beast DDR5 32GB 6000MHz",
  "title": "金士顿 FURY Beast DDR5 32GB 6000MHz 内存套装",

  "price": 699.0,
  "currency": "CNY",
  "is_available": true,

  "specs": {
    "memory_type": "DDR5",
    "total_capacity_gb": 32,
    "stick_count": 2,
    "capacity_per_stick_gb": 16,
    "speed_mhz": 6000,
    "latency_cl": 30,
    "has_rgb": false
  },

  "scores": {
    "gaming": 8.0,
    "development": 7.5,
    "multitask": 7.5,
    "value": 8.0
  },

  "compatibility": {
    "requires_motherboard_memory_type": "DDR5",
    "requires_memory_slots": 2,
    "recommended_for_development": false,
    "recommended_for_heavy_development": false
  },

  "tags": ["DDR5", "32GB", "6000MHz", "双通道", "无RGB"],

  "selling_points": [
    "32GB 容量适合游戏和普通开发",
    "6000MHz 频率适合主流 DDR5 平台",
    "双条套装可启用双通道"
  ],

  "limitations": [
    "重度 Android 开发、虚拟机和 Docker 场景可能更适合 64GB",
    "需要确认主板支持 DDR5"
  ],

  "recommendation_text": "适合游戏和中轻度开发；如果经常开模拟器、Docker 和数据库，建议升级到 64GB。",

  "source": {
    "platform": "JD",
    "sku_id": null,
    "source_url": null,
    "raw_specs_text": null,
    "updated_at": "2026-05-25 12:00:00"
  }
}
```

重点字段：

```text
specs.memory_type
specs.total_capacity_gb
specs.stick_count
specs.speed_mhz
```

---

## 5. SSD / 硬盘商品卡 JSON

```json
{
  "part_id": "storage_wd_sn850x_2tb",
  "part_type": "storage",
  "brand": "WD",
  "model": "Black SN850X 2TB",
  "title": "西部数据 WD_BLACK SN850X 2TB NVMe SSD",

  "price": 999.0,
  "currency": "CNY",
  "is_available": true,

  "specs": {
    "storage_type": "SSD",
    "capacity_gb": 2048,
    "interface": "M.2",
    "protocol": "NVMe PCIe 4.0",
    "form_factor": "M.2 2280",
    "read_speed_mb_s": 7300,
    "write_speed_mb_s": 6600,
    "endurance_tbw": 1200,
    "has_dram_cache": true
  },

  "scores": {
    "game_loading": 9.0,
    "development": 9.0,
    "large_file_transfer": 9.0,
    "value": 7.5
  },

  "compatibility": {
    "requires_m2_slot": true,
    "requires_nvme_support": true,
    "best_with_pcie_version": "PCIe 4.0"
  },

  "tags": ["2TB", "NVMe", "PCIe 4.0", "M.2 2280", "带缓存"],

  "selling_points": [
    "2TB 容量适合游戏、开发项目和素材共存",
    "PCIe 4.0 NVMe 性能较强",
    "适合作为系统盘和主力盘"
  ],

  "limitations": [
    "价格高于普通 PCIe 3.0 SSD",
    "需要主板具备 M.2 NVMe 插槽"
  ],

  "recommendation_text": "适合预算较充足、希望系统盘和游戏盘合一的用户。",

  "source": {
    "platform": "JD",
    "sku_id": null,
    "source_url": null,
    "raw_specs_text": null,
    "updated_at": "2026-05-25 12:00:00"
  }
}
```

重点字段：

```text
specs.capacity_gb
specs.interface
specs.protocol
specs.form_factor
specs.read_speed_mb_s
specs.write_speed_mb_s
```

---

## 6. 电源商品卡 JSON

```json
{
  "part_id": "psu_seasonic_focus_gx_750",
  "part_type": "psu",
  "brand": "Seasonic",
  "model": "FOCUS GX-750",
  "title": "海韵 FOCUS GX-750 金牌全模组电源",

  "price": 799.0,
  "currency": "CNY",
  "is_available": true,

  "specs": {
    "wattage": 750,
    "efficiency_rating": "80Plus Gold",
    "modular_type": "full_modular",
    "has_12vhpwr": false,
    "has_12v_2x6": false,
    "pcie_8pin_count": 4,
    "cpu_8pin_count": 2,
    "length_mm": 140,
    "warranty_years": 10
  },

  "scores": {
    "reliability": 9.0,
    "noise": 8.0,
    "value": 8.0,
    "upgrade_margin": 8.0
  },

  "compatibility": {
    "supports_required_total_wattage": 750,
    "supports_pcie_8pin_gpu": true,
    "supports_12v_2x6_gpu": false,
    "requires_case_psu_length_mm": 140
  },

  "tags": ["750W", "金牌", "全模组", "高可靠性"],

  "selling_points": [
    "750W 功率适合多数中高端游戏平台",
    "全模组设计便于走线",
    "可靠性评分较高，适合长期使用"
  ],

  "limitations": [
    "不带原生 12V-2x6 接口时，搭配新款 NVIDIA 高端卡需要注意转接线",
    "价格高于普通铜牌电源"
  ],

  "recommendation_text": "适合中高端游戏主机和开发主机，优先保证稳定性。",

  "source": {
    "platform": "JD",
    "sku_id": null,
    "source_url": null,
    "raw_specs_text": null,
    "updated_at": "2026-05-25 12:00:00"
  }
}
```

重点字段：

```text
specs.wattage
specs.efficiency_rating
specs.has_12v_2x6
specs.pcie_8pin_count
scores.reliability
```

---

## 7. 散热器商品卡 JSON

```json
{
  "part_id": "cooler_thermalright_pa120",
  "part_type": "cooler",
  "brand": "Thermalright",
  "model": "Peerless Assassin 120",
  "title": "利民 Peerless Assassin 120 双塔风冷散热器",

  "price": 199.0,
  "currency": "CNY",
  "is_available": true,

  "specs": {
    "cooler_type": "air",
    "supported_sockets": ["AM4", "AM5", "LGA1700", "LGA1851"],
    "cooling_capacity_w": 220,
    "height_mm": 155,
    "radiator_size_mm": null,
    "fan_count": 2,
    "noise_db": 25.6,
    "has_rgb": false
  },

  "scores": {
    "cooling": 8.5,
    "noise": 8.0,
    "value": 9.0,
    "installation": 7.5
  },

  "compatibility": {
    "supports_cpu_sockets": ["AM4", "AM5", "LGA1700", "LGA1851"],
    "requires_case_cpu_cooler_height_mm": 155,
    "recommended_cpu_tdp_w_max": 180
  },

  "tags": ["双塔风冷", "AM5", "LGA1700", "高性价比", "无RGB"],

  "selling_points": [
    "散热能力足够压制多数中高端 CPU",
    "价格较低，性价比高",
    "无需水冷维护风险"
  ],

  "limitations": [
    "高度 155mm，需要确认机箱散热器限高",
    "大型双塔可能影响部分高马甲内存安装"
  ],

  "recommendation_text": "适合大多数游戏和开发主机，是原型项目里较稳妥的默认散热选项。",

  "source": {
    "platform": "JD",
    "sku_id": null,
    "source_url": null,
    "raw_specs_text": null,
    "updated_at": "2026-05-25 12:00:00"
  }
}
```

重点字段：

```text
specs.supported_sockets
specs.cooling_capacity_w
specs.height_mm
specs.radiator_size_mm
specs.noise_db
```

---

## 8. 机箱商品卡 JSON

```json
{
  "part_id": "case_lianli_a3_matx",
  "part_type": "case",
  "brand": "LIAN LI",
  "model": "A3-mATX",
  "title": "联力 A3-mATX 机箱",

  "price": 499.0,
  "currency": "CNY",
  "is_available": true,

  "specs": {
    "case_type": "M-ATX",
    "supported_motherboard_form_factors": ["M-ATX", "Mini-ITX"],
    "max_gpu_length_mm": 415,
    "max_cpu_cooler_height_mm": 165,
    "max_psu_length_mm": 220,
    "supported_radiator_sizes": [240, 360],
    "included_fans": 0,
    "max_fans": 10,
    "has_front_usb_c": true,
    "has_rgb": false
  },

  "scores": {
    "airflow": 8.0,
    "noise": 7.5,
    "build_difficulty": 7.0,
    "compatibility_space": 8.5
  },

  "compatibility": {
    "supports_motherboard_form_factors": ["M-ATX", "Mini-ITX"],
    "supports_gpu_length_mm": 415,
    "supports_cpu_cooler_height_mm": 165,
    "supports_psu_length_mm": 220,
    "supports_front_usb_c": true
  },

  "tags": ["M-ATX", "紧凑机箱", "支持长显卡", "前置 Type-C", "无RGB"],

  "selling_points": [
    "体积相对紧凑，但显卡兼容空间较好",
    "支持 M-ATX 主板，适合主流装机",
    "前置 USB-C 方便日常使用"
  ],

  "limitations": [
    "默认风扇数量可能不足，需要额外购买机箱风扇",
    "紧凑机箱装机难度高于普通中塔"
  ],

  "recommendation_text": "适合希望控制体积但仍然使用中高端显卡的用户。",

  "source": {
    "platform": "JD",
    "sku_id": null,
    "source_url": null,
    "raw_specs_text": null,
    "updated_at": "2026-05-25 12:00:00"
  }
}
```

重点字段：

```text
specs.supported_motherboard_form_factors
specs.max_gpu_length_mm
specs.max_cpu_cooler_height_mm
scores.airflow
specs.has_front_usb_c
```

---

## 推荐系统读取 JSON 的方式

不再写 SQL 查询，而是用 Python 读取 JSON 文件并建立内存索引。

```python
import json
from pathlib import Path
from typing import Any, Dict, List


PARTS_PATH = Path("data/parts.json")


def load_parts() -> List[Dict[str, Any]]:
    if not PARTS_PATH.exists():
        return []

    with PARTS_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_parts(parts: List[Dict[str, Any]]) -> None:
    PARTS_PATH.parent.mkdir(parents=True, exist_ok=True)

    with PARTS_PATH.open("w", encoding="utf-8") as f:
        json.dump(parts, f, ensure_ascii=False, indent=2)


def upsert_part(part: Dict[str, Any]) -> None:
    parts = load_parts()
    part_id = part["part_id"]

    for index, old_part in enumerate(parts):
        if old_part.get("part_id") == part_id:
            parts[index] = part
            save_parts(parts)
            return

    parts.append(part)
    save_parts(parts)


def build_part_index(parts: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {
        part["part_id"]: part
        for part in parts
    }


def filter_parts_by_type(parts: List[Dict[str, Any]], part_type: str) -> List[Dict[str, Any]]:
    return [part for part in parts if part.get("part_type") == part_type]
```

## 字段映射关系

```text
原 cpus 表          -> part_type = "cpu"
原 motherboards 表  -> part_type = "motherboard"
原 gpus 表          -> part_type = "gpu"
原 memories 表      -> part_type = "memory"
原 storages 表      -> part_type = "storage"
原 psus 表          -> part_type = "psu"
原 coolers 表       -> part_type = "cooler"
原 cases 表         -> part_type = "case"
```

这一版的核心变化是：不再维护 8 张 SQL 表，而是维护一个统一的 `parts.json`。每个对象自带规格、评分、兼容性、推荐解释和来源信息。这样更适合项目原型、比赛展示和快速迭代。


# 十七、统一配件表方案(略，不关注)

如果你不想一开始建很多表，也可以先用统一 parts 表。

```sql
CREATE TABLE parts (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,

    part_type VARCHAR(50),
    brand VARCHAR(50),
    model VARCHAR(100),

    price DECIMAL(10,2),
    currency VARCHAR(20),

    specs JSON,
    scores JSON,
    compatibility JSON,

    source_url TEXT,
    source_platform VARCHAR(100),

    is_available BOOLEAN,
    updated_at DATETIME
);
```

例如显卡：

```json
{
  "part_type": "gpu",
  "brand": "NVIDIA",
  "model": "RTX 5070",
  "price": 4599,
  "specs": {
    "vram_gb": 12,
    "vram_type": "GDDR7",
    "tdp_w": 250,
    "length_mm": 300,
    "recommended_psu_w": 750,
    "power_connector": "12V-2x6"
  },
  "scores": {
    "gaming_1080p": 9.2,
    "gaming_1440p": 8.8,
    "gaming_4k": 7.1,
    "ai": 7.8
  },
  "compatibility": {
    "requires_psu_w": 750,
    "requires_case_gpu_length_mm": 300
  }
}
```

早期项目可以用统一 parts 表，后期再拆成多表。

---

# 十八、规则引擎设计

你需要把“装机规则”写成代码，而不是只放在 Prompt 里。

---

## 1. 用途 → 预算权重规则

```json
{
  "mainstream_gaming": {
    "gpu": [0.35, 0.45],
    "cpu": [0.15, 0.25],
    "motherboard": [0.08, 0.12],
    "memory": [0.08, 0.12],
    "storage": [0.08, 0.12],
    "psu_case_cooling": [0.12, 0.18]
  },
  "development": {
    "cpu": [0.25, 0.35],
    "gpu": [0.10, 0.20],
    "memory": [0.15, 0.25],
    "storage": [0.10, 0.20],
    "motherboard": [0.08, 0.12],
    "psu_case_cooling": [0.10, 0.15]
  },
  "ai_creation": {
    "gpu": [0.40, 0.60],
    "cpu": [0.15, 0.25],
    "memory": [0.10, 0.20],
    "storage": [0.10, 0.15],
    "motherboard_psu_cooling": [0.15, 0.25]
  }
}
```

---

## 2. 用途 → 最低硬件要求

```json
{
  "android_development": {
    "min_cpu_cores": 6,
    "recommended_cpu_cores": 8,
    "min_memory_gb": 32,
    "recommended_memory_gb": 64,
    "min_storage_gb": 1000,
    "recommended_storage_gb": 2000
  },
  "mainstream_gaming_1440p": {
    "min_memory_gb": 32,
    "recommended_gpu_tier": "mid_high",
    "recommended_storage_gb": 2000
  },
  "ai_creation": {
    "min_memory_gb": 64,
    "min_vram_gb": 12,
    "recommended_vram_gb": 16,
    "require_cuda": true
  }
}
```

---

## 3. 兼容性规则

```python
def check_compatibility(build):
    errors = []
    warnings = []

    if build.cpu.socket != build.motherboard.socket:
        errors.append("CPU 与主板接口不匹配")

    if build.memory.memory_type != build.motherboard.memory_type:
        errors.append("内存类型与主板不匹配")

    if build.motherboard.form_factor not in build.case.supported_motherboard_form_factors:
        errors.append("主板尺寸与机箱不兼容")

    if build.gpu.length_mm > build.case.max_gpu_length_mm:
        errors.append("显卡长度超过机箱限制")

    if build.cooler.height_mm and build.cooler.height_mm > build.case.max_cpu_cooler_height_mm:
        errors.append("CPU 散热器高度超过机箱限制")

    if build.psu.wattage < build.gpu.recommended_psu_w:
        errors.append("电源功率不足")

    if build.cpu.tdp_w > build.cooler.cooling_capacity_w:
        warnings.append("CPU 散热余量较小")

    return {
        "passed": len(errors) == 0,
        "errors": errors,
        "warnings": warnings
    }
```

---

# 十九、方案评分规则

每套方案都应有评分，方便排序和对比。

```python
overall_score = (
    usage_match_score * 0.30 +
    compatibility_score * 0.25 +
    budget_fit_score * 0.15 +
    performance_balance_score * 0.15 +
    reliability_score * 0.10 +
    upgrade_score * 0.05
)
```

不同用途可以调整权重。

---

## 游戏用户评分

```python
overall_score = (
    gaming_performance_score * 0.35 +
    compatibility_score * 0.25 +
    budget_fit_score * 0.15 +
    reliability_score * 0.10 +
    upgrade_score * 0.10 +
    noise_score * 0.05
)
```

---

## 开发用户评分

```python
overall_score = (
    cpu_productivity_score * 0.25 +
    memory_score * 0.25 +
    storage_score * 0.15 +
    compatibility_score * 0.20 +
    budget_fit_score * 0.10 +
    reliability_score * 0.05
)
```

---

## AI 用户评分

```python
overall_score = (
    gpu_ai_score * 0.30 +
    vram_score * 0.25 +
    memory_score * 0.15 +
    storage_score * 0.10 +
    compatibility_score * 0.15 +
    budget_fit_score * 0.05
)
```

---

# 二十、方案对比逻辑

每次生成新版方案后，调用 compare_builds。

```python
def compare_builds(old_build, new_build):
    changed_parts = {}

    for part in ["cpu", "gpu", "memory", "storage", "motherboard", "psu", "cooler", "case"]:
        if old_build[part].id != new_build[part].id:
            changed_parts[part] = {
                "old": old_build[part].model,
                "new": new_build[part].model,
                "price_diff": new_build[part].price - old_build[part].price
            }

    score_diff = {
        "gaming": new_build.scores.gaming - old_build.scores.gaming,
        "development": new_build.scores.development - old_build.scores.development,
        "ai": new_build.scores.ai - old_build.scores.ai,
        "budget_fit": new_build.scores.budget_fit - old_build.scores.budget_fit,
        "overall": new_build.scores.overall - old_build.scores.overall
    }

    return {
        "changed_parts": changed_parts,
        "score_diff": score_diff,
        "price_diff": new_build.total_price - old_build.total_price
    }
```

---

# 二十一、前端展示推荐流程

前端最好按“方案演进”展示，而不是只展示当前方案。

```text
用户需求
  ↓
V1 粗方案
  ↓
系统追问
  ↓
用户补充
  ↓
V2 优化方案
  ↓
V1 vs V2 对比
  ↓
继续追问
  ↓
V3 最终方案
```

---

## 推荐展示结构

```text
当前推荐：V2 开发增强版

配置表：
CPU：Ryzen 7 7700
主板：B650M
显卡：RTX 5060 Ti
内存：64GB DDR5
硬盘：2TB NVMe SSD
电源：750W 金牌
散热：双塔风冷
机箱：M-ATX 风道机箱

相比 V1：
- CPU 多核能力提升
- 内存从 32GB 提升到 64GB
- 显卡可能从 RTX 5070 降到 RTX 5060 Ti
- 游戏性能略降
- Android 开发体验明显提升

系统建议：
如果你更看重 2K 144Hz 游戏，保留 V1。
如果你更看重 Android 开发、多任务和 Docker，选择 V2。
```

---

# 二十二、完整业务流程图

```text
开始
  ↓
用户输入需求
  ↓
LLM 抽取结构化需求
  ↓
检查预算、用途、显示器、是否二手等关键字段
  ↓
是否信息足够？
  ├─ 否 → 基于默认假设生成粗方案 + 追问
  └─ 是 → 直接生成候选方案
  ↓
选择推荐模板
  ↓
根据模板分配预算权重
  ↓
从配件库/RAG 中检索候选配件
  ↓
生成多个候选组合
  ↓
兼容性校验
  ↓
淘汰不兼容组合
  ↓
计算评分
  ↓
选择 Top 3 方案
  ↓
生成当前版本方案
  ↓
是否存在上一版？
  ├─ 否 → 只输出粗方案和追问
  └─ 是 → 输出新旧方案对比
  ↓
判断是否仍有关键缺失信息
  ├─ 是 → 继续追问
  └─ 否 → 输出最终推荐
```

---

# 二十三、完整 Prompt 工作流

你可以把系统 Prompt 分成 4 类。

---

## 1. 需求解析 Prompt

```text
请从用户输入中抽取装机需求，输出 JSON。

需要抽取：
- 预算
- 是否包含显示器
- 用途
- 用途优先级
- 游戏分辨率
- 目标帧率
- 游戏类型
- 开发工具
- 是否需要 Docker/虚拟机/模拟器
- 是否需要 AI
- 是否接受二手
- 是否有品牌偏好
- 是否有机箱尺寸偏好
- 缺失字段

只输出 JSON，不要输出自然语言。
```

---

## 2. 追问生成 Prompt

```text
你是装机推荐系统。

请根据当前用户画像和缺失字段，生成最多 3 个追问问题。

追问原则：
1. 优先询问会显著改变配置的问题。
2. 不要问已经知道的信息。
3. 每个问题要简短。
4. 如果可以生成粗方案，则先生成粗方案，再追问。
```

---

## 3. 方案生成 Prompt

```text
你是装机推荐系统。

请基于以下结构化需求和候选配件，生成一套电脑主机方案。

必须遵守：
1. 所有配件必须兼容。
2. 总价应接近预算。
3. 根据用途分配预算权重。
4. 游戏优先显卡。
5. 开发优先 CPU、内存和 SSD。
6. AI 优先 NVIDIA 显卡和显存。
7. 不推荐杂牌电源。
8. 输出配置表、价格、理由、潜在缺点和升级建议。
```

---

## 4. 方案对比 Prompt

```text
你是装机方案对比助手。

请比较旧方案和新方案。

输出：
1. 变化的配件
2. 为什么变化
3. 新方案优点
4. 新方案缺点
5. 旧方案仍然适合哪些情况
6. 新方案更适合哪些情况
7. 给用户的选择建议
```

---

# 二十四、一个完整示例流程

## Round 1：用户输入

```text
我想装一台 8000 元电脑，打游戏，也做安卓开发。
```

---

## 系统解析

```json
{
  "budget": 8000,
  "usage": ["gaming", "android_development"],
  "target_resolution": null,
  "target_fps": null,
  "development_tools": ["Android Studio"],
  "need_docker": null,
  "includes_monitor": null,
  "missing_fields": [
    "includes_monitor",
    "target_resolution",
    "target_fps",
    "need_docker"
  ]
}
```

---

## 系统生成 V1 粗方案

```text
V1 粗方案：游戏均衡版

CPU：Ryzen 5 9600X
主板：B650M
显卡：RTX 5070
内存：32GB DDR5
硬盘：2TB NVMe SSD
电源：750W 金牌
散热：双塔风冷
机箱：M-ATX 风道机箱

假设：
- 预算只包含主机
- 游戏目标为 2K
- Android 开发强度中等
```

---

## 系统追问

```text
为了进一步优化，我需要确认：

1. 8000 元预算是否只包含主机？
2. 你主要玩 1080p、2K 还是 4K？
3. Android 开发时是否经常开模拟器、Docker 或数据库？
```

---

## Round 2：用户回答

```text
只包含主机，主要玩 2K 144Hz。开发时会开模拟器、Docker 和 MySQL。
```

---

## 系统更新需求

```json
{
  "budget": 8000,
  "includes_monitor": false,
  "usage": ["gaming", "android_development"],
  "usage_priority": {
    "gaming": 0.55,
    "android_development": 0.45
  },
  "target_resolution": "1440p",
  "target_fps": 144,
  "need_android_emulator": true,
  "need_docker": true,
  "need_database": true,
  "recommended_memory_gb": 64
}
```

---

## 系统生成 V2

```text
V2：开发增强均衡版

CPU：Ryzen 7 7700
主板：B650M
显卡：RTX 5060 Ti / RTX 5070
内存：64GB DDR5
硬盘：2TB NVMe SSD
电源：750W 金牌
散热：双塔风冷
机箱：M-ATX 风道机箱
```

---

## V1 vs V2 对比

| 项目   | V1 游戏均衡版      | V2 开发增强版               | 变化原因                          |
| ---- | ------------- | ---------------------- | ----------------------------- |
| CPU  | Ryzen 5 9600X | Ryzen 7 7700           | 开发多任务更需要 8 核                  |
| 显卡   | RTX 5070      | RTX 5060 Ti / RTX 5070 | 为内存和 CPU 腾预算                  |
| 内存   | 32GB          | 64GB                   | 模拟器 + Docker + MySQL 更适合 64GB |
| 硬盘   | 2TB           | 2TB                    | 保持不变                          |
| 游戏体验 | 更强            | 略弱或接近                  | 取决于是否保留 RTX 5070              |
| 开发体验 | 中等偏上          | 明显更好                   | 多任务更稳定                        |

系统建议：

```text
如果你更重视 2K 144Hz 游戏，选择 V1。
如果你更重视 Android 开发、模拟器、Docker 和数据库，选择 V2。
如果你想兼顾 V2 的 64GB 内存和 V1 的 RTX 5070，建议预算提高到 8500-9000 元。
```

---

# 二十五、最终推荐工作流总结

你可以把整个项目流程定为：

```text
1. 接收用户自然语言需求
2. 抽取结构化需求
3. 判断信息完整度
4. 基于默认假设生成 V1 粗方案
5. 生成最多 3 个关键追问
6. 用户补充信息
7. 更新用户画像
8. 根据新画像生成新 Prompt
9. 检索配件候选
10. 组合生成候选方案
11. 兼容性校验
12. 评分重排
13. 生成 V2/V3/V4 新方案
14. 每次新方案都与上一版比较
15. 输出优缺点和选择建议
16. 当关键字段齐全后，输出最终推荐
```

---

# 二十六、最重要的实现原则

这个项目不要完全依赖大模型“自由发挥”。

推荐系统应该是：

```text
规则引擎：负责能不能装、是否合理
数据库：负责配件参数、价格、库存、性能指标
RAG：负责补充知识、品牌差异、解释依据
LLM：负责理解用户意图、追问、解释取舍
版本管理：负责记录每一次方案演进
对比模块：负责让用户理解为什么方案变化
```

你的核心竞争力不是“给出一套配置”，而是：

> **让用户在多轮对话中逐步明确需求，并且每一次修改都能看到方案如何变化、为什么变化、代价是什么、收益是什么。**
