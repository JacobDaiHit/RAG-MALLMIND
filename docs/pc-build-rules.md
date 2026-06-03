可以。你现在需要的不是“百科级完全兼容规则”，而是一套**比赛 / Demo 级可落地的合格整机判定规则**：

> 只要一组配件通过这些规则，就可以认为它是一套“可组装、可解释、可加入购物车”的合格电脑主机方案。

下面我给你一套可以直接写进代码里的规则体系。

---

# 一、先定义“合格主机方案”包含哪些配件

一台基础电脑主机至少需要这 8 类：

```text
CPU
主板 Motherboard
显卡 GPU
内存 RAM
硬盘 Storage / SSD
电源 PSU
机箱 Case
散热器 CPU Cooler
```

其中有些可以条件省略：

| 配件   | 是否必须 | 说明                                          |
| ---- | ---: | ------------------------------------------- |
| CPU  |   必须 | 主机核心                                        |
| 主板   |   必须 | 所有配件连接中心                                    |
| 内存   |   必须 | 至少 1 条                                      |
| 硬盘   |   必须 | 至少 1 个系统盘                                   |
| 电源   |   必须 | 提供整机供电                                      |
| 机箱   |   必须 | 用于安装主板、电源、显卡、散热                             |
| 散热器  | 通常必须 | 如果 CPU 自带散热器且功耗较低，可以认为满足                    |
| 独立显卡 | 条件必须 | 如果 CPU 有核显且用户只是办公，可以不配；游戏 / 剪辑 / AI 场景建议必须配 |

所以你的程序里可以先做第一条规则：

```python
required_categories = ["cpu", "motherboard", "memory", "storage", "psu", "case"]

if usage in ["gaming", "ai", "video_editing", "3d_rendering"]:
    required_categories.append("gpu")

if not cpu.has_stock_cooler:
    required_categories.append("cooler")
```

---

# 二、P0 级规则：通过这些，就可以认为“能装起来”

这是最重要的一层。建议你们先实现这 10 条。

---

## 规则 1：所有配件必须真实存在且有库存

### 判断逻辑

```python
for part in build.parts:
    assert part.product_id in database
    assert part.stock > 0
```

### 为什么重要

防止大模型编造商品、价格、库存。

### 输出示例

```json
{
  "rule": "商品真实性与库存",
  "status": "passed",
  "evidence": "8 个配件均存在于商品库，且库存均大于 0"
}
```

---

## 规则 2：总价不能超过预算

### 判断逻辑

```python
total_price = sum(part.price for part in build.parts)

if user.budget_type == "hard":
    assert total_price <= user.budget

if user.budget_type == "around":
    assert total_price <= user.budget * 1.05
```

### 推荐约定

| 用户说法      | 预算类型     | 允许范围      |
| --------- | -------- | --------- |
| “5000 以内” | hard     | `<= 5000` |
| “5000 左右” | soft     | `<= 5250` |
| “大概 5000” | soft     | `<= 5500` |
| “越便宜越好”   | minimize | 尽量低于预算    |

### 输出示例

```json
{
  "rule": "预算约束",
  "status": "passed",
  "evidence": "总价 4869 元，低于预算 5000 元"
}
```

---

## 规则 3：CPU 插槽必须匹配主板插槽

这是最核心的硬兼容规则。

### 必要字段

CPU：

```json
{
  "socket": "LGA1700"
}
```

主板：

```json
{
  "socket": "LGA1700"
}
```

### 判断逻辑

```python
assert cpu.socket == motherboard.socket
```

### 示例

| CPU     | 主板      | 是否兼容 |
| ------- | ------- | ---- |
| AM5     | AM5     | 是    |
| AM4     | AM5     | 否    |
| LGA1700 | LGA1700 | 是    |
| LGA1200 | LGA1700 | 否    |

### 输出示例

```json
{
  "rule": "CPU 与主板插槽",
  "status": "passed",
  "evidence": "CPU 插槽为 LGA1700，主板插槽为 LGA1700"
}
```

---

## 规则 4：CPU 代际 / 芯片组要兼容

只判断 socket 还不够，因为同一个插槽下，不同芯片组可能支持情况不同。

### 简化规则

你可以维护一张 `chipset_cpu_rules` 表。

```json
[
  {
    "platform": "Intel",
    "socket": "LGA1700",
    "chipset": "B660",
    "supported_cpu_generations": ["12th", "13th"],
    "bios_update_required_for": ["13th"]
  },
  {
    "platform": "Intel",
    "socket": "LGA1700",
    "chipset": "B760",
    "supported_cpu_generations": ["12th", "13th", "14th"],
    "bios_update_required_for": []
  },
  {
    "platform": "AMD",
    "socket": "AM5",
    "chipset": "B650",
    "supported_cpu_generations": ["Ryzen 7000", "Ryzen 8000", "Ryzen 9000"],
    "bios_update_required_for": ["Ryzen 9000"]
  }
]
```

### 判断逻辑

```python
rule = find_chipset_rule(cpu.socket, motherboard.chipset)

assert cpu.generation in rule.supported_cpu_generations
```

### BIOS 风险处理

如果只是可能需要 BIOS 更新，不一定判失败，可以判为 warning：

```json
{
  "rule": "CPU 代际与主板芯片组",
  "status": "warning",
  "evidence": "主板 B660 支持 13 代 CPU，但可能需要 BIOS 更新"
}
```

### 建议

比赛 Demo 里可以先简化：

```python
if cpu.socket == motherboard.socket:
    chipset_status = "passed"
```

然后把 BIOS 风险作为 P1 规则，不要一开始做太复杂。

---

## 规则 5：内存类型必须匹配主板

### 必要字段

主板：

```json
{
  "memory_type": "DDR4",
  "memory_slots": 4,
  "max_memory_gb": 128
}
```

内存：

```json
{
  "memory_type": "DDR4",
  "capacity_gb": 16,
  "module_count": 2
}
```

### 判断逻辑

```python
assert memory.memory_type == motherboard.memory_type
assert memory.module_count <= motherboard.memory_slots
assert memory.capacity_gb <= motherboard.max_memory_gb
```

### 示例

| 主板      | 内存      | 是否兼容 |
| ------- | ------- | ---- |
| DDR4 主板 | DDR4 内存 | 是    |
| DDR4 主板 | DDR5 内存 | 否    |
| DDR5 主板 | DDR5 内存 | 是    |
| 2 插槽主板  | 4 条内存   | 否    |

### 输出示例

```json
{
  "rule": "主板与内存兼容",
  "status": "passed",
  "evidence": "主板支持 DDR4，内存为 DDR4；主板 4 插槽，当前内存 2 条"
}
```

---

## 规则 6：主板板型必须被机箱支持

### 必要字段

主板：

```json
{
  "form_factor": "M-ATX"
}
```

机箱：

```json
{
  "supported_motherboard_form_factors": ["ITX", "M-ATX", "ATX"]
}
```

### 判断逻辑

```python
assert motherboard.form_factor in case.supported_motherboard_form_factors
```

### 常见板型大小

从小到大：

```text
ITX < M-ATX < ATX < E-ATX
```

### 输出示例

```json
{
  "rule": "主板与机箱板型",
  "status": "passed",
  "evidence": "主板为 M-ATX，机箱支持 ITX / M-ATX / ATX"
}
```

---

## 规则 7：显卡长度必须小于机箱显卡限长

### 必要字段

显卡：

```json
{
  "length_mm": 245,
  "slot_width": 2.0
}
```

机箱：

```json
{
  "max_gpu_length_mm": 330
}
```

### 判断逻辑

```python
assert gpu.length_mm <= case.max_gpu_length_mm
```

### 推荐留余量

最好不要刚好卡死。建议：

```python
assert gpu.length_mm <= case.max_gpu_length_mm - 10
```

也就是至少留 10mm。

### 输出示例

```json
{
  "rule": "显卡与机箱空间",
  "status": "passed",
  "evidence": "显卡长度 245mm，机箱最大支持 330mm，余量 85mm"
}
```

---

## 规则 8：CPU 散热器必须支持 CPU 插槽

### 必要字段

散热器：

```json
{
  "supported_sockets": ["LGA1700", "AM4", "AM5"]
}
```

CPU：

```json
{
  "socket": "LGA1700"
}
```

### 判断逻辑

```python
assert cpu.socket in cooler.supported_sockets
```

### 输出示例

```json
{
  "rule": "散热器插槽支持",
  "status": "passed",
  "evidence": "散热器支持 LGA1700，CPU 插槽为 LGA1700"
}
```

---

## 规则 9：风冷高度 / 水冷冷排必须适配机箱

这条要根据散热器类型分开判断。

### 风冷

散热器：

```json
{
  "cooler_type": "air",
  "height_mm": 154
}
```

机箱：

```json
{
  "max_cpu_cooler_height_mm": 165
}
```

判断：

```python
if cooler.cooler_type == "air":
    assert cooler.height_mm <= case.max_cpu_cooler_height_mm
```

### 水冷

散热器：

```json
{
  "cooler_type": "aio",
  "radiator_size_mm": 240
}
```

机箱：

```json
{
  "radiator_support_mm": [120, 240, 280, 360]
}
```

判断：

```python
if cooler.cooler_type == "aio":
    assert cooler.radiator_size_mm in case.radiator_support_mm
```

### 输出示例

```json
{
  "rule": "散热器与机箱空间",
  "status": "passed",
  "evidence": "风冷高度 154mm，机箱限高 165mm"
}
```

---

## 规则 10：电源功率必须足够

这是非常重要的规则。

### 必要字段

CPU：

```json
{
  "tdp_watt": 65
}
```

GPU：

```json
{
  "tdp_watt": 115,
  "recommended_psu_watt": 550
}
```

电源：

```json
{
  "wattage": 650
}
```

### 简化估算公式

```python
estimated_power = cpu.tdp_watt + gpu.tdp_watt + 100
required_psu = estimated_power * 1.3
assert psu.wattage >= required_psu
```

其中 `+100` 是给主板、内存、硬盘、风扇、USB 设备等留出的基础功耗。

### 示例

```text
CPU TDP = 65W
GPU TDP = 115W
其他配件 = 100W
估算功耗 = 280W
推荐电源 = 280 * 1.3 = 364W
实际电源 = 550W
结果：通过
```

### 更稳的公式

```python
estimated_power = (
    cpu.tdp_watt
    + gpu.tdp_watt
    + motherboard.power_watt
    + memory.module_count * 5
    + storage.count * 8
    + fan_count * 3
    + 50
)

required_psu = estimated_power * 1.35
```

### 输出示例

```json
{
  "rule": "电源功率",
  "status": "passed",
  "evidence": "估算整机峰值功耗 310W，建议电源 >= 419W，当前电源 650W"
}
```

---

# 三、P1 级规则：通过这些，就可以认为“方案比较靠谱”

P0 规则保证“能装起来”，P1 规则保证“更像专业装机方案”。

---

## 规则 11：电源接口必须满足显卡供电需求

### 必要字段

显卡：

```json
{
  "power_connectors": ["8pin"]
}
```

电源：

```json
{
  "pcie_8pin_count": 2,
  "pcie_6pin_count": 0,
  "has_12vhpwr": false,
  "has_12v_2x6": false
}
```

### 判断逻辑

如果显卡需要 8pin：

```python
required_8pin = gpu.power_connectors.count("8pin")
assert psu.pcie_8pin_count >= required_8pin
```

如果显卡需要 12VHPWR：

```python
if "12vhpwr" in gpu.power_connectors:
    assert psu.has_12vhpwr or psu.has_12v_2x6
```

### 输出示例

```json
{
  "rule": "显卡供电接口",
  "status": "passed",
  "evidence": "显卡需要 1 个 8pin，电源提供 2 个 PCIe 8pin"
}
```

---

## 规则 12：电源规格必须适配机箱

### 必要字段

电源：

```json
{
  "form_factor": "ATX",
  "length_mm": 140
}
```

机箱：

```json
{
  "supported_psu_form_factors": ["ATX"],
  "max_psu_length_mm": 160
}
```

### 判断逻辑

```python
assert psu.form_factor in case.supported_psu_form_factors
assert psu.length_mm <= case.max_psu_length_mm
```

### 输出示例

```json
{
  "rule": "电源与机箱空间",
  "status": "passed",
  "evidence": "电源为 ATX，长度 140mm；机箱支持 ATX 电源，限长 160mm"
}
```

---

## 规则 13：SSD 接口必须被主板支持

### 必要字段

SSD：

```json
{
  "interface": "M.2 NVMe",
  "form_factor": "M.2 2280",
  "pcie_generation": "PCIe 4.0"
}
```

主板：

```json
{
  "m2_slots": 2,
  "sata_ports": 4,
  "supported_storage_interfaces": ["M.2 NVMe", "SATA"]
}
```

### 判断逻辑

```python
if storage.interface == "M.2 NVMe":
    assert motherboard.m2_slots >= number_of_m2_ssds

if storage.interface == "SATA":
    assert motherboard.sata_ports >= number_of_sata_drives
```

### PCIe 代际处理

PCIe 4.0 SSD 插到 PCIe 3.0 M.2 上通常可以用，只是降速，所以可以 warning，不一定 fail：

```json
{
  "rule": "SSD PCIe 代际",
  "status": "warning",
  "evidence": "PCIe 4.0 SSD 可安装在 PCIe 3.0 插槽中，但速度会降低"
}
```

---

## 规则 14：内存频率不应明显超出平台支持

### 必要字段

CPU：

```json
{
  "max_memory_speed_mhz": {
    "DDR4": 3200,
    "DDR5": 5600
  }
}
```

主板：

```json
{
  "max_memory_speed_mhz": 6400
}
```

内存：

```json
{
  "frequency_mhz": 6000
}
```

### 判断逻辑

```python
platform_max = min(cpu.max_memory_speed, motherboard.max_memory_speed)

if memory.frequency_mhz <= platform_max:
    status = "passed"
else:
    status = "warning"
```

为什么不是 fail？

因为高频内存通常可以降频运行，但你可以提示：

```json
{
  "rule": "内存频率",
  "status": "warning",
  "evidence": "内存标称 6400MHz，平台可能降频运行"
}
```

---

## 规则 15：散热器压制能力要覆盖 CPU 功耗

### 必要字段

CPU：

```json
{
  "tdp_watt": 125
}
```

散热器：

```json
{
  "tdp_rating_watt": 180
}
```

### 判断逻辑

```python
assert cooler.tdp_rating_watt >= cpu.tdp_watt * 1.2
```

### 推荐标准

| CPU 类型        | 散热建议            |
| ------------- | --------------- |
| 65W CPU       | 原装散热 / 低端塔扇可接受  |
| 90W-125W CPU  | 中端塔扇            |
| 125W+ 高性能 CPU | 高端风冷 / 240 水冷以上 |
| 超频 CPU        | 更高规格散热          |

### 输出示例

```json
{
  "rule": "CPU 散热能力",
  "status": "passed",
  "evidence": "CPU TDP 125W，散热器标称压制 180W"
}
```

---

## 规则 16：CPU 与 GPU 性能不要严重失衡

这不是硬兼容，但会影响方案质量。

### 给每个 CPU / GPU 一个性能等级

例如：

```json
{
  "cpu_performance_tier": 5,
  "gpu_performance_tier": 6
}
```

等级可以简单定义为 1-10。

### 判断逻辑

```python
gap = abs(cpu.performance_tier - gpu.performance_tier)

if gap <= 2:
    status = "passed"
elif gap <= 3:
    status = "warning"
else:
    status = "failed"
```

### 示例

| CPU 档位 | GPU 档位 | 结果           |
| -----: | -----: | ------------ |
|      5 |      6 | 合理           |
|      4 |      8 | 可能 CPU 瓶颈    |
|      8 |      3 | 显卡太弱，预算分配不合理 |

### 输出示例

```json
{
  "rule": "CPU 与 GPU 性能均衡",
  "status": "warning",
  "evidence": "CPU 档位 4，GPU 档位 8，部分 CPU 密集游戏可能存在瓶颈"
}
```

---

# 四、P2 级规则：让方案看起来更专业

这些不是必须，但很适合加分。

---

## 规则 17：用途匹配规则

不同用途下，配件权重不同。

### 办公主机

最低要求：

```text
CPU 有核显，或者配低端独显
内存 >= 16GB
SSD >= 512GB
不需要高端显卡
电源 400W-550W 即可
```

规则示例：

```python
if usage == "office":
    assert memory.capacity_gb >= 16
    assert storage.capacity_gb >= 512
    assert cpu.integrated_graphics or gpu is not None
```

---

### 1080P 游戏主机

最低要求：

```text
独立显卡
显存 >= 8GB
内存 >= 16GB
SSD >= 1TB 推荐
```

规则示例：

```python
if usage == "1080p_gaming":
    assert gpu is not None
    assert gpu.vram_gb >= 8
    assert memory.capacity_gb >= 16
```

---

### 2K 游戏主机

最低要求：

```text
GPU 性能等级 >= 6
显存 >= 8GB，最好 12GB+
内存 >= 16GB，推荐 32GB
电源留足余量
```

规则示例：

```python
if usage == "2k_gaming":
    assert gpu.performance_tier >= 6
    assert gpu.vram_gb >= 8
    assert memory.capacity_gb >= 16
```

---

### 剪辑 / 生产力主机

最低要求：

```text
CPU 核心数 >= 8 或线程数 >= 16
内存 >= 32GB
SSD >= 1TB
有独显更好
```

规则示例：

```python
if usage == "video_editing":
    assert cpu.core_count >= 8 or cpu.thread_count >= 16
    assert memory.capacity_gb >= 32
    assert storage.capacity_gb >= 1000
```

---

### AI / 深度学习入门主机

最低要求：

```text
NVIDIA GPU 优先
显存 >= 12GB 更好
内存 >= 32GB
SSD >= 1TB
电源充足
```

规则示例：

```python
if usage == "ai":
    assert gpu.brand == "NVIDIA"
    assert gpu.vram_gb >= 12
    assert memory.capacity_gb >= 32
```

---

## 规则 18：预算分配合理性

这个规则可以让你的方案显得像“懂装机”。

### 游戏主机预算比例建议

| 配件  |    推荐占比 |
| --- | ------: |
| GPU | 35%-45% |
| CPU | 12%-22% |
| 主板  |  8%-15% |
| 内存  |  5%-10% |
| SSD |  5%-10% |
| 电源  |   5%-8% |
| 机箱  |   4%-8% |
| 散热  |   3%-8% |

判断示例：

```python
gpu_ratio = gpu.price / total_price

if usage in ["gaming", "2k_gaming"]:
    assert gpu_ratio >= 0.30
```

如果 GPU 只占总价 15%，但用户说主要玩 3A，就应该 warning：

```json
{
  "rule": "预算分配",
  "status": "warning",
  "evidence": "该方案显卡预算占比仅 18%，对于游戏主机偏低"
}
```

---

## 规则 19：用户偏好 / 排除条件必须满足

用户可能说：

```text
不要 AMD
不要水冷
不要白色
不要海景房机箱
要小机箱
要 Wi-Fi
```

你应该把这些转成硬过滤规则。

### 示例

```json
{
  "exclude_brands": ["AMD"],
  "exclude_cooler_type": ["aio"],
  "required_color": "white",
  "required_wifi": true
}
```

判断逻辑：

```python
for part in build.parts:
    assert part.brand not in user.exclude_brands

if "aio" in user.exclude_cooler_type:
    assert cooler.cooler_type != "aio"

if user.required_wifi:
    assert motherboard.wifi == True
```

---

## 规则 20：升级空间规则

如果用户说：

```text
以后想升级显卡
想用久一点
后续可能加硬盘
```

则建议：

```python
assert psu.wattage >= required_psu + 150
assert motherboard.m2_slots >= 2
assert case.max_gpu_length_mm >= 320
```

输出：

```json
{
  "rule": "升级空间",
  "status": "passed",
  "evidence": "电源额定 750W，高于当前需求约 250W；主板提供 2 个 M.2 插槽"
}
```

---

# 五、建议你的最终判定标准

你可以把方案分为 4 个等级。

---

## 1. Failed：不合格方案

只要出现以下任意情况，就是不合格：

```text
缺少必要配件
商品不存在
商品无库存
CPU 与主板插槽不匹配
主板与内存类型不匹配
主板装不进机箱
显卡装不进机箱
散热器不支持 CPU 插槽
电源功率明显不足
总价超过硬预算
违反用户明确排除条件
```

程序里可以这样写：

```python
if any(rule.status == "failed" and rule.severity == "hard" for rule in report):
    build.status = "invalid"
```

---

## 2. Passed with Warnings：可用但有提示

比如：

```text
可能需要 BIOS 更新
内存可能降频
电源余量较小
CPU/GPU 有轻微性能失衡
散热压制能力刚好够
```

这种方案可以推荐，但必须提示风险。

```json
{
  "status": "valid_with_warnings",
  "summary": "该方案可以组装，但存在 2 个注意事项"
}
```

---

## 3. Passed：合格方案

满足：

```text
所有硬规则通过
总价在预算内
核心用途满足
没有严重性能瓶颈
```

输出：

```json
{
  "status": "valid",
  "summary": "该方案通过基础兼容性校验，可以作为合格整机方案"
}
```

---

## 4. Excellent：优秀方案

满足：

```text
所有硬规则通过
无明显 warning
预算分配合理
用途匹配度高
有升级空间
解释充分
```

输出：

```json
{
  "status": "excellent",
  "summary": "该方案兼容性、预算利用、用途匹配和升级空间均表现较好"
}
```

---

# 六、你可以直接采用的规则配置格式

建议你们把规则写成配置，而不是全写死在 prompt 里。

```json
{
  "hard_rules": [
    "all_required_parts_present",
    "all_products_exist",
    "all_products_in_stock",
    "total_price_within_budget",
    "cpu_socket_matches_motherboard",
    "cpu_generation_supported_by_chipset",
    "memory_type_matches_motherboard",
    "memory_capacity_within_motherboard_limit",
    "motherboard_form_factor_supported_by_case",
    "gpu_length_within_case_limit",
    "cooler_socket_supports_cpu",
    "cooler_fits_case",
    "psu_wattage_sufficient",
    "psu_connectors_satisfy_gpu",
    "storage_interface_supported_by_motherboard",
    "user_exclusion_constraints_satisfied"
  ],
  "soft_rules": [
    "psu_has_enough_margin",
    "cpu_gpu_performance_balanced",
    "memory_frequency_reasonable",
    "budget_allocation_reasonable",
    "usage_requirement_satisfied",
    "upgrade_space_available",
    "aesthetic_preference_satisfied"
  ]
}
```

---

# 七、推荐的数据字段设计

为了支持上面的规则，每个类目至少需要这些字段。

---

## CPU

```json
{
  "product_id": "cpu_001",
  "category": "cpu",
  "name": "Intel Core i5-12400F",
  "brand": "Intel",
  "price": 799,
  "stock": 10,
  "socket": "LGA1700",
  "generation": "12th",
  "core_count": 6,
  "thread_count": 12,
  "tdp_watt": 65,
  "integrated_graphics": false,
  "supported_memory_types": ["DDR4", "DDR5"],
  "performance_tier": 5,
  "has_stock_cooler": true
}
```

---

## 主板

```json
{
  "product_id": "mb_001",
  "category": "motherboard",
  "name": "B760M DDR4 Motherboard",
  "brand": "MSI",
  "price": 699,
  "stock": 8,
  "socket": "LGA1700",
  "chipset": "B760",
  "memory_type": "DDR4",
  "memory_slots": 4,
  "max_memory_gb": 128,
  "form_factor": "M-ATX",
  "m2_slots": 2,
  "sata_ports": 4,
  "wifi": false,
  "supported_storage_interfaces": ["M.2 NVMe", "SATA"]
}
```

---

## 内存

```json
{
  "product_id": "ram_001",
  "category": "memory",
  "name": "16GB DDR4 3200 8Gx2",
  "brand": "Kingston",
  "price": 259,
  "stock": 20,
  "memory_type": "DDR4",
  "capacity_gb": 16,
  "module_count": 2,
  "frequency_mhz": 3200
}
```

---

## 显卡

```json
{
  "product_id": "gpu_001",
  "category": "gpu",
  "name": "GeForce RTX 4060 8GB",
  "brand": "NVIDIA",
  "price": 2299,
  "stock": 6,
  "chipset": "RTX 4060",
  "vram_gb": 8,
  "tdp_watt": 115,
  "recommended_psu_watt": 550,
  "length_mm": 245,
  "slot_width": 2.0,
  "power_connectors": ["8pin"],
  "interface": "PCIe 4.0 x8",
  "performance_tier": 6
}
```

---

## SSD

```json
{
  "product_id": "ssd_001",
  "category": "storage",
  "name": "1TB NVMe SSD",
  "brand": "WD",
  "price": 399,
  "stock": 15,
  "storage_type": "SSD",
  "capacity_gb": 1000,
  "interface": "M.2 NVMe",
  "form_factor": "M.2 2280",
  "pcie_generation": "PCIe 4.0"
}
```

---

## 电源

```json
{
  "product_id": "psu_001",
  "category": "psu",
  "name": "650W 80Plus Bronze",
  "brand": "Great Wall",
  "price": 399,
  "stock": 12,
  "wattage": 650,
  "efficiency_rating": "80Plus Bronze",
  "form_factor": "ATX",
  "length_mm": 140,
  "pcie_8pin_count": 2,
  "pcie_6pin_count": 0,
  "has_12vhpwr": false,
  "has_12v_2x6": false
}
```

---

## 机箱

```json
{
  "product_id": "case_001",
  "category": "case",
  "name": "M-ATX Gaming Case",
  "brand": "SAMA",
  "price": 249,
  "stock": 9,
  "supported_motherboard_form_factors": ["ITX", "M-ATX"],
  "max_gpu_length_mm": 330,
  "max_cpu_cooler_height_mm": 165,
  "supported_psu_form_factors": ["ATX"],
  "max_psu_length_mm": 160,
  "radiator_support_mm": [120, 240],
  "color": "black"
}
```

---

## 散热器

```json
{
  "product_id": "cooler_001",
  "category": "cooler",
  "name": "4 Heatpipe Tower Cooler",
  "brand": "Thermalright",
  "price": 89,
  "stock": 20,
  "cooler_type": "air",
  "supported_sockets": ["LGA1700", "AM4", "AM5"],
  "tdp_rating_watt": 180,
  "height_mm": 154,
  "radiator_size_mm": null
}
```

---

# 八、最终校验函数的伪代码

你可以直接照这个结构写。

```python
def validate_pc_build(build, user_constraints):
    report = []

    cpu = build.get("cpu")
    motherboard = build.get("motherboard")
    gpu = build.get("gpu")
    memory = build.get("memory")
    storage = build.get("storage")
    psu = build.get("psu")
    case = build.get("case")
    cooler = build.get("cooler")

    # 1. 必要配件
    required = ["cpu", "motherboard", "memory", "storage", "psu", "case"]
    if user_constraints["usage"] in ["gaming", "ai", "video_editing"]:
        required.append("gpu")

    for category in required:
        if not build.get(category):
            report.append(fail("必要配件", f"缺少 {category}", hard=True))

    # 2. 商品真实性与库存
    for part in build.parts:
        if not product_exists(part.product_id):
            report.append(fail("商品真实性", f"{part.name} 不存在", hard=True))
        if part.stock <= 0:
            report.append(fail("库存", f"{part.name} 无库存", hard=True))

    # 3. 预算
    total_price = sum(part.price for part in build.parts)
    if total_price > user_constraints["budget"]:
        report.append(fail("预算", f"总价 {total_price} 超过预算", hard=True))
    else:
        report.append(pass_("预算", f"总价 {total_price} 在预算内"))

    # 4. CPU - 主板
    if cpu.socket != motherboard.socket:
        report.append(fail("CPU 与主板插槽", "插槽不匹配", hard=True))
    else:
        report.append(pass_("CPU 与主板插槽", f"{cpu.socket} 匹配"))

    # 5. 主板 - 内存
    if memory.memory_type != motherboard.memory_type:
        report.append(fail("内存类型", "内存类型与主板不匹配", hard=True))
    else:
        report.append(pass_("内存类型", f"{memory.memory_type} 匹配"))

    if memory.module_count > motherboard.memory_slots:
        report.append(fail("内存插槽数量", "内存条数量超过主板插槽", hard=True))

    # 6. 主板 - 机箱
    if motherboard.form_factor not in case.supported_motherboard_form_factors:
        report.append(fail("主板与机箱", "机箱不支持该主板板型", hard=True))
    else:
        report.append(pass_("主板与机箱", "板型兼容"))

    # 7. GPU - 机箱
    if gpu:
        if gpu.length_mm > case.max_gpu_length_mm:
            report.append(fail("显卡长度", "显卡过长，机箱装不下", hard=True))
        else:
            report.append(pass_("显卡长度", "显卡长度满足机箱限制"))

    # 8. 散热器 - CPU / 机箱
    if cooler:
        if cpu.socket not in cooler.supported_sockets:
            report.append(fail("散热器插槽", "散热器不支持 CPU 插槽", hard=True))

        if cooler.cooler_type == "air":
            if cooler.height_mm > case.max_cpu_cooler_height_mm:
                report.append(fail("散热器高度", "风冷高度超过机箱限高", hard=True))

        if cooler.cooler_type == "aio":
            if cooler.radiator_size_mm not in case.radiator_support_mm:
                report.append(fail("水冷冷排", "机箱不支持该冷排尺寸", hard=True))

        if cooler.tdp_rating_watt < cpu.tdp_watt * 1.2:
            report.append(warn("散热能力", "散热器压制能力偏紧"))

    # 9. 电源功率
    gpu_power = gpu.tdp_watt if gpu else 0
    estimated_power = cpu.tdp_watt + gpu_power + 100
    required_psu = estimated_power * 1.3

    if psu.wattage < required_psu:
        report.append(fail("电源功率", "电源功率不足", hard=True))
    else:
        report.append(pass_("电源功率", f"估算需求 {required_psu:.0f}W，电源 {psu.wattage}W"))

    # 10. 显卡供电接口
    if gpu:
        required_8pin = gpu.power_connectors.count("8pin")
        if psu.pcie_8pin_count < required_8pin:
            report.append(fail("显卡供电接口", "电源 PCIe 8pin 不足", hard=True))

        if "12vhpwr" in gpu.power_connectors:
            if not psu.has_12vhpwr and not psu.has_12v_2x6:
                report.append(fail("显卡供电接口", "电源缺少 12VHPWR / 12V-2x6", hard=True))

    # 11. SSD
    if storage.interface == "M.2 NVMe" and motherboard.m2_slots < 1:
        report.append(fail("硬盘接口", "主板没有 M.2 插槽", hard=True))

    # 12. 用户排除条件
    for part in build.parts:
        if part.brand in user_constraints.get("exclude_brands", []):
            report.append(fail("用户排除条件", f"用户排除了品牌 {part.brand}", hard=True))

    # 13. 最终状态
    hard_failed = any(item["status"] == "failed" and item["hard"] for item in report)
    warnings = [item for item in report if item["status"] == "warning"]

    if hard_failed:
        status = "invalid"
    elif warnings:
        status = "valid_with_warnings"
    else:
        status = "valid"

    return {
        "status": status,
        "total_price": total_price,
        "estimated_power": estimated_power,
        "required_psu": required_psu,
        "report": report
    }
```

---

# 九、最终推荐你们项目采用的合格标准

你可以在项目里直接定义：

```text
一套电脑主机方案被认为是合格方案，当且仅当：

1. 必要配件完整；
2. 所有商品均来自商品库，且有库存；
3. 总价满足用户预算；
4. CPU 与主板插槽匹配；
5. CPU 代际与主板芯片组兼容，或仅存在可解释的 BIOS 风险；
6. 主板、CPU、内存三者的内存类型兼容；
7. 主板板型被机箱支持；
8. 显卡长度不超过机箱限长；
9. 散热器支持 CPU 插槽，并且能装入机箱；
10. 电源功率满足整机估算功耗并保留安全余量；
11. 电源接口满足显卡供电需求；
12. 硬盘接口被主板支持；
13. 不违反用户明确的品牌、颜色、尺寸、散热方式等排除条件。
```

更简洁一点，可以写成代码里的判断：

```python
qualified = (
    required_parts_present
    and products_exist
    and products_in_stock
    and total_price <= budget
    and cpu.socket == motherboard.socket
    and memory.memory_type == motherboard.memory_type
    and motherboard.form_factor in case.supported_motherboard_form_factors
    and gpu.length_mm <= case.max_gpu_length_mm
    and cpu.socket in cooler.supported_sockets
    and cooler_fits_case
    and psu.wattage >= estimated_power * 1.3
    and psu_connectors_satisfy_gpu
    and storage_supported_by_motherboard
    and user_constraints_satisfied
)
```

---

# 十、最小实现版本

如果你们时间紧，我建议先只做这个最小规则集：

```text
1. 配件完整
2. 商品存在且有库存
3. 总价 <= 预算
4. CPU socket == 主板 socket
5. 内存类型 == 主板内存类型
6. 主板板型 in 机箱支持板型
7. 显卡长度 <= 机箱显卡限长
8. CPU socket in 散热器支持列表
9. 散热器高度 <= 机箱散热限高
10. 电源功率 >= (CPU TDP + GPU TDP + 100) * 1.3
11. 电源接口满足显卡
12. SSD 接口被主板支持
```

这 12 条足够支撑你们在 Demo 里说：

**我们的 PC 整机推荐不是大模型随便生成，而是所有推荐方案都经过了结构化兼容性规则校验。**
