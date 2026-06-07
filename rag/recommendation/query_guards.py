"""Small deterministic guards for recommendation routing and candidate filtering."""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional


PC_QUERY_TERMS = [
    "装机",
    "cpu",
    "处理器",
    "显卡",
    "gpu",
    "rtx",
    "4060",
    "4070",
    "4080",
    "4090",
    "主板",
    "内存",
    "ddr4",
    "ddr5",
    "固态",
    "ssd",
    "硬盘",
    "电源",
    "机箱",
    "散热",
    "风冷",
    "水冷",
]

PC_QUERY_TERMS.extend(["主机", "整机", "核显", "电脑配置"])

UNSUPPORTED_CATEGORY_TERMS = ["宠物", "猫粮", "狗粮", "自动喂食器", "家电", "冰箱", "洗衣机", "汽车", "轿车", "suv", "电动车", "摩托车"]
SAFETY_RESTRICTED_CATEGORY_TERMS = ["医药", "处方药", "感冒药"]
PREMIUM_TERMS = ["旗舰", "高端", "顶配", "拍照旗舰", "影像旗舰", "rtx 4090", "4090"]

PRODUCT_TYPE_TERMS: Dict[str, Iterable[str]] = {
    "phone": ["手机", "智能手机", "拍照手机", "旗舰手机", "影像旗舰", "iphone"],
    "earphone": ["耳机", "蓝牙耳机", "降噪耳机", "真无线耳机", "airpods", "freebuds"],
    "laptop": ["笔记本", "轻薄本", "电脑", "laptop", "matebook", "macbook", "thinkbook", "thinkpad"],
    "tablet": ["平板", "pad", "ipad", "matepad"],
    "beverage": ["饮料", "无糖饮料", "气泡水", "茶饮", "可乐", "苏打水"],
    "coffee": ["咖啡"],
    "nuts_snack": ["坚果", "零食礼盒", "每日坚果", "混合坚果"],
    "serum": ["精华", "护肤精华"],
    "cleanser": ["洁面", "洗面奶", "cleanser", "facial cleanser"],
    "cream": ["面霜", "保湿霜", "修护霜"],
    "base_makeup": ["底妆", "粉底", "持妆", "遮瑕", "蜜粉", "散粉", "粉饼", "定妆"],
    "sunscreen": ["防晒"],
    "tshirt": ["t恤", "短袖", "速干t"],
    "jacket": ["外套", "冲锋衣", "夹克", "防风衣", "风衣"],
    "hat": ["帽", "帽子", "遮阳帽", "鸭舌帽"],
    "basketball_shoes": ["篮球鞋", "篮球实战鞋", "实战篮球鞋", "实战鞋", "篮球专业比赛鞋", "篮球比赛鞋"],
    "shoes": ["运动鞋", "跑鞋", "跑步鞋", "篮球鞋", "徒步鞋", "鞋"],
    "watch": ["手表", "运动手表", "智能手表", "watch"],
    "dress": ["裙子", "连衣裙", "半身裙", "裙装"],
    "game_console": ["游戏机", "ps5", "playstation", "xbox", "switch", "nintendo"],
}

PRODUCT_TYPE_SUBCATEGORY_TERMS: Dict[str, Iterable[str]] = {
    "phone": ["智能手机", "手机"],
    "earphone": ["耳机", "真无线耳机"],
    "laptop": ["笔记本", "轻薄本", "笔记本电脑"],
    "tablet": ["平板", "平板电脑"],
    "beverage": ["茶饮", "碳酸饮料", "功能饮料", "饮料"],
    "coffee": ["咖啡"],
    "nuts_snack": ["坚果", "零食", "坚果/零食"],
    "serum": ["精华"],
    "cleanser": ["洁面", "洗面奶", "cleanser", "facial cleanser"],
    "cream": ["面霜"],
    "base_makeup": ["底妆", "粉底", "遮瑕", "蜜粉", "散粉", "粉饼", "定妆"],
    "sunscreen": ["防晒"],
    "tshirt": ["t恤", "短袖", "速干t恤"],
    "jacket": ["外套", "冲锋衣", "夹克", "防风衣", "风衣"],
    "hat": ["帽", "帽子", "遮阳帽", "鸭舌帽"],
    "basketball_shoes": ["篮球鞋", "篮球实战鞋", "实战篮球鞋", "实战鞋", "篮球专业比赛鞋", "篮球比赛鞋"],
    "shoes": ["鞋", "跑步鞋", "篮球鞋", "徒步鞋", "运动鞋"],
    "watch": ["手表", "运动手表", "智能手表"],
    "dress": ["裙子", "连衣裙", "半身裙"],
    "game_console": ["游戏机", "主机游戏"],
}

PRODUCT_TYPE_LABELS: Dict[str, str] = {
    "phone": "手机",
    "earphone": "耳机",
    "laptop": "笔记本",
    "tablet": "平板",
    "beverage": "饮料",
    "coffee": "咖啡",
    "nuts_snack": "坚果/零食",
    "serum": "精华",
    "cleanser": "洗面奶",
    "cream": "面霜",
    "base_makeup": "底妆",
    "sunscreen": "防晒",
    "tshirt": "T恤",
    "jacket": "外套",
    "hat": "帽子",
    "basketball_shoes": "篮球鞋",
    "shoes": "鞋",
    "watch": "手表",
    "dress": "裙子",
    "game_console": "游戏机",
}

PRODUCT_TYPE_CATEGORY: Dict[str, str] = {
    "phone": "digital",
    "earphone": "digital",
    "laptop": "digital",
    "tablet": "digital",
    "beverage": "food",
    "coffee": "food",
    "nuts_snack": "food",
    "serum": "beauty",
    "cleanser": "beauty",
    "cream": "beauty",
    "base_makeup": "beauty",
    "sunscreen": "beauty",
    "tshirt": "clothing",
    "jacket": "clothing",
    "hat": "clothing",
    "basketball_shoes": "clothing",
    "shoes": "clothing",
    "watch": "digital",
    "dress": "clothing",
    "game_console": "digital",
}

NEIGHBOR_TYPE_TERMS: Dict[str, Dict[str, Iterable[str]]] = {
    "clothing": {
        "pants": ["裤", "长裤", "瑜伽裤", "户外裤"],
        "backpack": ["背包", "双肩包"],
    }
}


def is_pc_query(message: str) -> bool:
    text = normalize_text(message)
    return any(normalize_text(term) in text for term in PC_QUERY_TERMS)


def parse_pc_part_constraints(query: str) -> Dict[str, Any]:
    raw = str(query or "")
    text = raw.lower()
    compact = normalize_text(raw)
    constraints: Dict[str, Any] = {}

    capacity = _parse_capacity_gb(text, compact)
    if capacity and any(term in compact for term in ("ssd", "固态", "硬盘", "存储")):
        constraints["storage_capacity"] = capacity
    if capacity and any(term in compact for term in ("内存", "ddr4", "ddr5", "memory")):
        constraints["memory_capacity"] = capacity

    if "ddr5" in compact:
        constraints["memory_type"] = "DDR5"
    elif "ddr4" in compact:
        constraints["memory_type"] = "DDR4"

    gpu_chipsets = _parse_gpu_chipsets(text)
    if gpu_chipsets:
        constraints["gpu_chipset"] = gpu_chipsets

    chipset = _parse_motherboard_chipset(text)
    if chipset:
        constraints["motherboard_chipset"] = chipset

    psu_wattage = _parse_psu_wattage(text)
    if psu_wattage:
        constraints["psu_wattage"] = psu_wattage
    if "金牌" in compact or "gold" in compact:
        constraints["psu_efficiency"] = "Gold"

    if any(term in compact for term in ("大显卡", "长显卡", "能装大显卡")):
        constraints["case_large_gpu"] = True

    cpu = _parse_cpu_constraint(text, compact)
    constraints.update(cpu)

    budget = _parse_budget_max(text)
    if budget is not None:
        constraints["budget_max"] = budget
    return constraints


def product_matches_pc_constraints(product: Any, constraints: Dict[str, Any]) -> bool:
    if not constraints:
        return True
    category = str(getattr(_get(product, "category"), "value", _get(product, "category")) or "")
    specs = product_specs(product)
    text = product_type_text(product)

    if category == "pc_storage" and constraints.get("storage_capacity"):
        return _numeric_equal(specs.get("capacity_gb"), constraints["storage_capacity"]) or _capacity_token_matches(text, constraints["storage_capacity"])
    if category == "pc_memory":
        if constraints.get("memory_capacity") and not (
            _numeric_equal(specs.get("capacity_gb"), constraints["memory_capacity"])
            or _capacity_token_matches(text, constraints["memory_capacity"])
        ):
            return False
        if constraints.get("memory_type"):
            spec_memory_type = specs.get("memory_type")
            if spec_memory_type:
                if normalize_text(spec_memory_type) != normalize_text(constraints["memory_type"]):
                    return False
            elif normalize_text(constraints["memory_type"]) not in text:
                return False
        return bool(constraints.get("memory_capacity") or constraints.get("memory_type"))
    if category == "pc_gpu" and constraints.get("gpu_chipset"):
        product_chipset = normalize_text(specs.get("chipset") or specs.get("gpu_chip") or _get(product, "sub_category") or _get(product, "title"))
        return any(_gpu_chipset_matches(product_chipset, chipset) for chipset in constraints["gpu_chipset"])
    if category == "pc_cpu" and (constraints.get("cpu_brand") or constraints.get("cpu_series")):
        if constraints.get("cpu_brand") and normalize_text(constraints["cpu_brand"]) not in text:
            return False
        if constraints.get("cpu_series") and normalize_text(constraints["cpu_series"]) not in text:
            return False
        return True
    if category == "pc_motherboard" and (constraints.get("motherboard_chipset") or constraints.get("memory_type")):
        if constraints.get("motherboard_chipset") and normalize_text(specs.get("chipset")) != normalize_text(constraints["motherboard_chipset"]):
            return False
        if constraints.get("memory_type") and normalize_text(specs.get("memory_type")) != normalize_text(constraints["memory_type"]):
            return False
        return True
    if category == "pc_psu" and (constraints.get("psu_wattage") or constraints.get("psu_efficiency")):
        if constraints.get("psu_wattage") and not _numeric_equal(specs.get("wattage_w"), constraints["psu_wattage"]):
            return False
        if constraints.get("psu_efficiency") and normalize_text(constraints["psu_efficiency"]) not in normalize_text(specs.get("efficiency_rating")):
            return False
        return True
    if category == "pc_case" and constraints.get("case_large_gpu"):
        try:
            return float(specs.get("gpu_clearance_mm") or specs.get("max_gpu_length_mm") or 0) >= 360
        except (TypeError, ValueError):
            return False
    return True


def product_pc_constraint_bonus(product: Any, constraints: Dict[str, Any]) -> float:
    if not constraints:
        return 0.0
    category = str(getattr(_get(product, "category"), "value", _get(product, "category")) or "")
    specs = product_specs(product)
    bonus = 0.0
    if category == "pc_case" and constraints.get("case_large_gpu"):
        try:
            clearance = float(specs.get("gpu_clearance_mm") or specs.get("max_gpu_length_mm") or 0)
        except (TypeError, ValueError):
            clearance = 0.0
        if clearance >= 400:
            bonus += 0.16
        elif clearance >= 360:
            bonus += 0.11
    return bonus


def product_query_preference_match(product: Any, query: str) -> bool:
    text = product_type_text(product)
    query_text = normalize_text(query)
    if any(term in query_text for term in ("苹果手机", "iphone", "apple")):
        return "apple" in text or "iphone" in text
    if any(term in query_text for term in ("遮阳帽", "鸭舌帽", "帽子", "帽")):
        category = str(getattr(_get(product, "category"), "value", _get(product, "category")) or "")
        return category == "clothing" and any(term in text for term in ("帽", "遮阳帽", "鸭舌帽"))
    if any(term in query_text for term in ("底妆", "粉底", "持妆", "遮瑕")):
        return any(term in text for term in ("底妆", "粉底", "遮瑕", "蜜粉", "散粉", "粉饼", "定妆"))
    return True


def requested_missing_subcategory(query: str, products: Iterable[Any]) -> Optional[Dict[str, Any]]:
    requested_type = infer_product_type(query)
    requested_category = category_for_product_type(requested_type)
    if not requested_type or not requested_category:
        return None
    products = list(products)
    scoped_products = [
        product
        for product in products
        if str(getattr(_get(product, "category"), "value", _get(product, "category")) or "") == requested_category
    ]
    if any(product_matches_type(product, requested_type) for product in scoped_products):
        return None
    neighbor_types = available_neighbor_product_types(scoped_products, requested_category, exclude=requested_type)
    return {
        "no_match_reason": "missing_subcategory",
        "requested_product_type": requested_type,
        "requested_product_type_label": PRODUCT_TYPE_LABELS.get(requested_type, requested_type),
        "available_neighbor_types": neighbor_types,
    }


def available_neighbor_product_types(products: Iterable[Any], category: str, *, exclude: str = "") -> List[str]:
    products = list(products)
    neighbors: List[str] = []
    for product_type, product_category in PRODUCT_TYPE_CATEGORY.items():
        if product_type == exclude or product_category != category:
            continue
        if any(product_matches_type(product, product_type) for product in products):
            label = PRODUCT_TYPE_LABELS.get(product_type, product_type)
            if label not in neighbors:
                neighbors.append(label)
    for neighbor, terms in NEIGHBOR_TYPE_TERMS.get(category, {}).items():
        if neighbor not in neighbors and any(any(term in product_type_text(product) for term in terms) for product in products):
            neighbors.append(neighbor)
    return neighbors


def _parse_capacity_gb(text: str, compact: str) -> Optional[int]:
    match = re.search(r"(\d+(?:\.\d+)?)\s*tb", text, re.I)
    if match:
        return int(float(match.group(1)) * 1000)
    match = re.search(r"(\d+)\s*(?:gb|g)(?![a-z])", text, re.I)
    if match:
        return int(match.group(1))
    for token, gb in {"500gb": 500, "1tb": 1000, "2tb": 2000, "4tb": 4000, "16gb": 16, "32gb": 32, "64gb": 64}.items():
        if token in compact:
            return gb
    return None


def _parse_gpu_chipsets(text: str) -> List[str]:
    patterns = [
        r"rtx\s*4060\s*ti",
        r"rtx\s*4070\s*ti",
        r"rtx\s*4060",
        r"rtx\s*4070",
        r"rtx\s*4080",
        r"rtx\s*4090",
        r"rx\s*7600",
        r"rx\s*7700",
        r"rx\s*7800",
    ]
    found = []
    occupied: List[range] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.I):
            span = range(match.start(), match.end())
            if any(match.start() >= item.start and match.end() <= item.stop for item in occupied):
                continue
            found.append(re.sub(r"\s+", " ", match.group(0).upper()))
            occupied.append(span)
    # Queries like "RTX 4060或4060 Ti" often omit the second RTX.
    if any(item == "RTX 4060" for item in found) and re.search(r"4060\s*ti", text, re.I):
        found.insert(0, "RTX 4060 Ti")
    return list(dict.fromkeys(found))


def _parse_motherboard_chipset(text: str) -> Optional[str]:
    match = re.search(r"\b([bhzx]\d{3}[a-z]?)\b", text, re.I)
    if match:
        return match.group(1).upper()
    return None


def _parse_psu_wattage(text: str) -> Optional[int]:
    match = re.search(r"(\d{3,4})\s*w", text, re.I)
    if match:
        return int(match.group(1))
    return None


def _gpu_chipset_matches(product_chipset: str, requested_chipset: str) -> bool:
    product_norm = normalize_text(product_chipset)
    requested_norm = normalize_text(requested_chipset)
    if requested_norm in {"rtx4070ti", "rtx4070tisuper"}:
        return product_norm in {"rtx4070ti", "rtx4070tisuper"} or product_norm.startswith("rtx4070ti")
    return product_norm == requested_norm or product_norm.startswith(requested_norm)


def _parse_cpu_constraint(text: str, compact: str) -> Dict[str, str]:
    constraints: Dict[str, str] = {}
    if "intel" in compact or re.search(r"\bi[3579]\b", text, re.I):
        constraints["cpu_brand"] = "Intel"
    if "ryzen" in compact or "锐龙" in compact:
        constraints["cpu_brand"] = "AMD"
    match = re.search(r"\bi([3579])\b", text, re.I)
    if match:
        constraints["cpu_series"] = f"i{match.group(1)}"
    match = re.search(r"ryzen\s*([579])", text, re.I)
    if match:
        constraints["cpu_series"] = f"Ryzen {match.group(1)}"
    return constraints


def _cn_unit_multiplier(unit_str: str) -> float:
    _m = {
        "亿": 100_000_000, "千万": 10_000_000, "百万": 1_000_000,
        "十万": 100_000, "万": 10_000, "千": 1_000, "百": 100,
    }
    return _m.get((unit_str or "").strip(), 1)


def _parse_budget_max(text: str) -> Optional[float]:
    match = re.search(r"(\d+(?:\.\d+)?)\s*(千万|百万|十万|万|千|百|亿)?\s*(?:元|块|rmb|cny)?\s*(?:以内|以下|内)", text, re.I)
    if match:
        return float(match.group(1)) * _cn_unit_multiplier(match.group(2) or "")
    return None


def _parse_budget_amount(text: str) -> Optional[float]:
    match = re.search(r"(\d+(?:\.\d+)?)\s*(千万|百万|十万|万|千|百|亿)?\s*(?:元|块|rmb|cny)", text, re.I)
    if match:
        return float(match.group(1)) * _cn_unit_multiplier(match.group(2) or "")
    return None


def _numeric_equal(value: Any, expected: int) -> bool:
    try:
        return int(float(value)) == int(expected)
    except (TypeError, ValueError):
        return False


def _capacity_token_matches(text: str, expected: int) -> bool:
    if expected >= 1000:
        tb = expected // 1000
        return f"{tb}tb" in text or f"{expected}gb" in text
    return f"{expected}gb" in text


def infer_product_type(query: str) -> Optional[str]:
    text = normalize_text(query)
    matched = []
    for product_type, terms in PRODUCT_TYPE_TERMS.items():
        if any(normalize_text(term) in text for term in terms):
            matched.append(product_type)
    for preferred in ("basketball_shoes", "hat", "base_makeup", "jacket"):
        if preferred in matched:
            return preferred
    return matched[0] if len(matched) == 1 else None


def category_for_product_type(product_type: Optional[str]) -> Optional[str]:
    if not product_type:
        return None
    return PRODUCT_TYPE_CATEGORY.get(product_type)


def product_matches_type(product: Any, product_type: Optional[str]) -> bool:
    if not product_type:
        return True
    text = product_type_text(product)
    terms = PRODUCT_TYPE_SUBCATEGORY_TERMS.get(product_type, [])
    return any(normalize_text(term) in text for term in terms)


def detect_no_match_reason(query: str, *, price_max: Optional[float] = None) -> Optional[str]:
    text = normalize_text(query)
    effective_price_max = price_max if price_max is not None else _parse_budget_amount(str(query or ""))
    if any(normalize_text(term) in text for term in SAFETY_RESTRICTED_CATEGORY_TERMS):
        return "safety_restricted_category"
    if any(normalize_text(term) in text for term in UNSUPPORTED_CATEGORY_TERMS):
        return "unsupported_category"
    if effective_price_max is not None and effective_price_max <= 100 and any(term in text for term in ("iphone", "苹果手机")):
        return "budget_impossible"
    if effective_price_max is not None and effective_price_max <= 500 and any(normalize_text(term) in text for term in PREMIUM_TERMS):
        return "budget_impossible"
    if effective_price_max is not None and effective_price_max <= 1000 and ("rtx4090" in text or "4090" in text):
        return "budget_impossible"
    return None


def product_type_text(product: Any) -> str:
    values = [
        _get(product, "title"),
        _get(product, "name"),
        _get(product, "product_name"),
        _get(product, "api_name"),
        _get(product, "sub_category"),
        _get(product, "category_name"),
        " ".join(str(item) for item in (_get(product, "tags") or [])),
        " ".join(str(item) for item in (_get(product, "best_for") or [])),
    ]
    return normalize_text(" ".join(str(item) for item in values if item))


def product_specs(product: Any) -> Dict[str, Any]:
    metadata = _get(product, "metadata") or {}
    if isinstance(metadata, dict):
        specs = metadata.get("specs")
        if isinstance(specs, dict):
            return specs
    specs = _get(product, "standardized_specs")
    return specs if isinstance(specs, dict) else {}


def normalize_text(value: object) -> str:
    return "".join(ch.lower() for ch in str(value or "") if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def clarification_required(query: str) -> Optional[Dict[str, Any]]:
    """Return a proactive clarification payload for broad covered requests."""

    text = normalize_text(query)
    raw = str(query or "")
    if not text or _allows_loose_recommendation(raw):
        return None
    if any(term in text for term in ["礼物", "送礼", "送女朋友", "送男朋友", "gift"]) and not any(
        term in text
        for term in ["预算", "以内", "以下", "护肤", "美妆", "数码", "衣服", "鞋", "零食", "咖啡", "香水", "口红"]
    ):
        return {
            "no_match_reason": "clarification_required",
            "clarification_required": True,
            "clarification_questions": [
                "预算上限大概是多少？",
                "更偏美妆护肤、数码小物、穿搭配饰还是食品饮料？",
                "收礼人的年龄、风格或忌讳品牌有没有需要避开的？",
            ],
            "requested_product_type": "gift",
        }
    product_type = infer_product_type(raw)
    if product_type == "phone" and _is_broad_single_recommendation(text):
        return {
            "no_match_reason": "clarification_required",
            "clarification_required": True,
            "clarification_questions": [
                "预算大概是多少？",
                "更看重拍照、续航、性能还是性价比？",
                "是否有品牌、系统或尺寸偏好？",
            ],
            "requested_product_type": "phone",
        }
    return None


def budget_relaxation_allowed(query: str) -> bool:
    text = normalize_text(query)
    return any(
        normalize_text(term) in text
        for term in ["可以贵一点", "预算可放宽", "预算可以放宽", "看看相近", "附近价位", "差不多也行", "相近价位"]
    )


def _is_broad_single_recommendation(text: str) -> bool:
    return any(term in text for term in ["推荐一款手机", "推荐个手机", "推荐手机", "买个手机"]) and not any(
        term in text
        for term in ["预算", "以内", "以下", "拍照", "续航", "性能", "游戏", "老人", "学生", "性价比", "旗舰", "小屏", "大屏"]
    )


def _allows_loose_recommendation(query: str) -> bool:
    return any(term in str(query or "") for term in ["先随便推荐", "直接推荐", "不用问", "随便推荐"])


def _get(value: Any, key: str) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)
