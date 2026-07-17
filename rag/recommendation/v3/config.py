"""Versioned, centralized V3 routing policy constants."""
from __future__ import annotations

GRAMMAR_VERSION = "1.1"
PROOF_VERSION = "rule-proof-v1"
REGISTRY_VERSION = "catalog-normalization-v2"
SEMANTIC_PARSE_POLICY_VERSION = "semantic-parse-v5"
SEMANTIC_PARSE_TIMEOUT_SECONDS = 18.0
CLARIFICATION_TTL_SECONDS = 10 * 60

# These are additional fail-closed guards.  Their absence never authorizes a
# local route; their presence always requires semantic parsing.
SEMANTIC_RISK_MARKERS = frozenset(
    {
        "可能",
        "或许",
        "虽然",
        "但是",
        "如果",
        "听说",
        "其实",
        "不一定",
        "差不多",
        "适合我",
        "给妈妈",
        "像上次",
        "不是让你",
        "不要只",
    }
)

POLITE_PREFIXES = ("请", "麻烦", "帮我")
POLITE_SUFFIXES = ("谢谢", "一下")
CONNECTORS = frozenset({"，", ",", "、", "然后", "另外", "最好"})

# This is deliberately tiny.  New aliases require catalog-backed tests.
PRODUCT_TYPE_ALIASES = {
    "phone": ("手机", "智能手机"),
    "tablet": ("平板", "平板电脑", "pad"),
    "earbuds": ("耳机", "真无线耳机"),
}

PRODUCT_TYPE_SUB_CATEGORIES = {
    "phone": ("智能手机",),
    "tablet": ("平板电脑",),
    "earbuds": ("真无线耳机",),
}

# Type candidates are assembled locally before the single SemanticParse call.
# A is never rank-truncated; B/C are independent lexical retrieval quotas.
TYPE_CANDIDATE_RETRIEVAL_VERSION = "type-candidates-v1"
TYPE_CANDIDATE_FULL_QUERY_LIMIT = 12
TYPE_CANDIDATE_ACTION_WINDOW_LIMIT = 8
TYPE_CANDIDATE_PROMPT_MAX_CHARS = 1800
TYPE_ACTION_ANCHORS = ("推荐", "想要", "来点", "给我", "帮我找", "配一台", "换一个")

# SemanticParse must cite one of these explicit user phrases before a first
# request may enter the PC solver.  This is a validator whitelist, not a local
# PC router: absent or unfamiliar wording asks a clarification instead.
PC_BUILD_EXPLICIT_SIGNAL_VERSION = "pc-build-signals-v1"
PC_BUILD_EXPLICIT_SIGNALS = ("配一台", "配台", "装一台", "装台", "组一台", "组台", "攒机", "装机", "配置单", "DIY", "diy")

# PC parts use catalog category rather than unstable manufacturer/model-specific
# sub-categories.  They are recommendation targets, not whole-PC build plans.
PC_COMPONENT_ALIASES = {
    "pc_motherboard": ("主板", "motherboard"),
    "pc_psu": ("电源", "psu", "power supply"),
    "pc_storage": ("固态硬盘", "ssd", "硬盘"),
    "pc_case": ("机箱", "case"),
    "pc_cpu": ("处理器", "cpu"),
    "pc_gpu": ("显卡", "gpu"),
    "pc_memory": ("内存", "memory"),
    "pc_cooler": ("散热器", "散热", "cooler"),
}

ATTRIBUTE_PREFERENCES = {
    "拍照优先": "camera",
    "续航优先": "battery",
    "轻薄优先": "lightweight",
}

SEMANTIC_ATTRIBUTE_ALIASES = {
    "camera": "camera",
    "拍照": "camera",
    "影像": "camera",
    "battery": "battery",
    "续航": "battery",
    "lightweight": "lightweight",
    "轻薄": "lightweight",
}

PRICE_MAX_MARKERS = ("以内", "以下", "不超过", "最多", "预算")
EXPLICIT_EXCLUDE_TEMPLATES = (
    "不要{brand}",
    "别要{brand}",
    "不考虑{brand}",
    "排除{brand}",
    "非{brand}",
    "{brand}以外",
    "除了{brand}",
    "不喜欢{brand}",
    "{brand}不好",
)

EXCLUDE_OPERATORS = ("不要", "排除", "不考虑")
RECOMMEND_VERBS = ("推荐", "来几款")
RECOMMEND_QUANTIFIERS = ("一款", "一个", "一双", "一台")
RECOMMEND_POLITE_SUFFIXES = ("谢谢", "一下")
EXPLICIT_PRODUCT_REQUEST_MARKERS = ("推荐", "购买", "买", "来一款", "来个", "给我找")

# A deliberately small follow-up grammar. Attribute-level questions remain
# with SemanticParse until their target and requested field can be proved.
CARD_FACT_QUERY_KINDS = {
    "参数": "specifications",
    "配置": "specifications",
    "sku": "skus",
    "SKU": "skus",
    "价格": "price",
}

BRAND_RELEASE_PREFIXES = ("要",)
BRAND_RELEASE_SUFFIXES = ("也可以",)
