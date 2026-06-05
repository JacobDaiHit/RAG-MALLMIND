import json
import sys
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.eval_retrieval import (
    case_scope,
    classify_case_group,
    constraint_violation,
    evaluate_cases,
    extract_ranked_product_ids,
    hit_at_k,
    load_cases,
    miss_stage_guess,
    mrr,
    product_title,
    precision_at_k,
    recall_at_k,
    validate_product_ids,
)
from rag.recommendation.query_guards import infer_product_type, is_pc_query, parse_pc_part_constraints
from rag.recommendation.recommendation_pipeline import recommend_shopping_products


class Product:
    def __init__(self, product_id, category="digital", title="", brand="", base_price=100, name=""):
        self.product_id = product_id
        self.id = product_id
        self.category = category
        self.category_name = category
        self.sub_category = ""
        self.title = title or product_id
        self.name = name
        self.brand = brand
        self.base_price = base_price
        self.tags = []
        self.best_for = []


def test_metric_functions():
    ranked = ["p1", "p2", "p3"]
    relevant = {"p2", "p4"}
    assert precision_at_k(ranked, relevant, 2) == 0.5
    assert recall_at_k(ranked, relevant, 2) == 0.5
    assert hit_at_k(ranked, relevant, 1) == 0
    assert hit_at_k(ranked, relevant, 2) == 1
    assert mrr(ranked, relevant) == 0.5


def test_constraint_violation_checks_ids_terms_and_price():
    catalog = {
        "p1": Product("p1", title="小米旗舰手机", brand="小米", base_price=4999),
        "p2": Product("p2", title="华为耳机", brand="华为", base_price=999),
    }
    violated, reasons = constraint_violation(
        ["p1", "p2"],
        {
            "excluded_product_ids": ["p2"],
            "must_not_contain_terms": ["小米"],
            "price_max": 3000,
        },
        catalog_by_id=catalog,
        k=2,
    )
    assert violated
    assert any("excluded_product_ids" in reason for reason in reasons)
    assert any("must_not_contain_terms" in reason for reason in reasons)
    assert any("price_above_max" in reason for reason in reasons)


def test_jsonl_case_loading():
    tmp_dir = Path(".pytest_tmp") / "retrieval_eval"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    path = tmp_dir / "cases.jsonl"
    path.write_text(
        json.dumps({"id": "c1", "query": "推荐耳机", "relevant_product_ids": ["p1"]}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    cases = load_cases(path)
    assert cases[0]["id"] == "c1"
    assert load_cases(path, case_id="missing") == []


def test_product_id_validation_warns_or_stricts():
    cases = [{"id": "c1", "query": "q", "relevant_product_ids": ["p1"], "acceptable_product_ids": ["p2"]}]
    catalog = {"p1": Product("p1")}
    assert validate_product_ids(cases, catalog) == {"c1": ["p2"]}
    with pytest.raises(ValueError):
        validate_product_ids(cases, catalog, strict=True)


def test_extract_ranked_product_ids_from_cards_plans_and_trace():
    result = {
        "product_cards": [{"product_id": "p1"}, {"id": "p2"}],
        "plans": [
            {
                "items": [{"product_id": "p3"}],
                "products": [{"product": {"product_id": "p4"}}],
                "components": [{"product": {"product_id": "p5"}}],
            }
        ],
        "trace": {"candidate_scope": {"top_candidates": [{"product_id": "p6"}]}},
    }
    assert extract_ranked_product_ids(result) == ["p1", "p2", "p3", "p4", "p5", "p6"]


def test_end_to_end_eval_with_fake_result():
    catalog = {
        "p1": Product("p1", category="digital"),
        "p2": Product("p2", category="digital"),
        "p3": Product("p3", category="food"),
    }
    cases = [
        {
            "id": "c1",
            "query": "推荐耳机",
            "relevant_product_ids": ["p2"],
            "acceptable_product_ids": ["p1"],
            "expected_categories": ["digital"],
        }
    ]

    def fake_recommend(case):
        return {"product_cards": [{"product_id": "p1"}, {"product_id": "p2"}, {"product_id": "p3"}]}, 12.0

    report = evaluate_cases(cases, top_ks=[1, 3], catalog_by_id=catalog, recommend_fn=fake_recommend)
    assert report["aggregate"]["case_count"] == 1
    assert report["aggregate"]["precision@1"] == 1.0
    assert report["aggregate"]["strict_recall@3"] == 1.0
    assert report["aggregate"]["category_accuracy@1"] == 1.0


def test_starter_cases_file_format():
    path = Path("tests/eval/golden_retrieval_cases.jsonl")
    cases = load_cases(path)
    assert 25 <= len(cases) <= 40
    assert all(isinstance(case["relevant_product_ids"], list) for case in cases)


def test_negative_case_is_excluded_from_main_retrieval_metrics():
    catalog = {"p1": Product("p1", category="digital")}
    cases = [
        {"id": "neg", "query": "no match", "relevant_product_ids": [], "acceptable_product_ids": [], "expected_behavior": "no_recommendation"},
        {"id": "pos", "query": "phone", "relevant_product_ids": ["p1"], "acceptable_product_ids": [], "expected_categories": ["digital"]},
    ]

    def fake_recommend(case):
        if case["id"] == "neg":
            return {"product_cards": [{"product_id": "p1"}]}, 1.0
        return {"product_cards": [{"product_id": "p1"}]}, 1.0

    report = evaluate_cases(cases, top_ks=[1, 5], catalog_by_id=catalog, recommend_fn=fake_recommend)
    assert report["main_summary"]["case_count"] == 1
    assert report["main_summary"]["metric_eligible_count"] == 1
    assert report["main_summary"]["hit@5"] == 1.0
    negative_case = next(case for case in report["per_case"] if case["id"] == "neg")
    assert negative_case["metrics"]["hit@5"] is None


def test_negative_case_returning_product_counts_as_violation():
    catalog = {"p1": Product("p1", category="digital")}
    cases = [
        {
            "id": "neg",
            "query": "pet food",
            "relevant_product_ids": [],
            "acceptable_product_ids": [],
            "expected_behavior": "clarify",
            "allow_fallback": False,
        }
    ]

    def fake_recommend(case):
        return {"product_cards": [{"product_id": "p1"}]}, 1.0

    report = evaluate_cases(cases, top_ks=[5], catalog_by_id=catalog, recommend_fn=fake_recommend)
    assert report["negative_summary"]["negative_case_count"] == 1
    assert report["negative_summary"]["negative_violation_rate"] == 1.0
    assert report["per_case"][0]["miss_stage_guess"] == "negative_fallback_error"


def test_pc_case_group_detection():
    case = {
        "id": "pc",
        "query": "cpu",
        "relevant_product_ids": ["pc_cpu_pc_seed_cpu_intel_core_i3_12100f"],
        "acceptable_product_ids": [],
        "expected_categories": ["pc_cpu"],
    }
    assert classify_case_group(case) == "pc"


def test_case_scope_defaults_and_catalog_gap_grouping():
    assert case_scope({"relevant_product_ids": ["p1"]}) == "in_catalog_exact"
    assert case_scope({"relevant_product_ids": [], "acceptable_product_ids": []}) == "negative_or_impossible"
    case = {
        "id": "gap",
        "query": "户外防风外套",
        "relevant_product_ids": [],
        "acceptable_product_ids": [],
        "expected_categories": ["clothing"],
        "case_scope": "catalog_gap",
    }
    assert case_scope(case) == "catalog_gap"
    assert classify_case_group(case) == "ecommerce"


def test_main_summary_uses_scope_not_surface_failures():
    catalog = {
        "p1": Product("p1", category="digital"),
        "p2": Product("p2", category="digital"),
        "p3": Product("p3", category="clothing"),
    }
    cases = [
        {"id": "exact", "query": "phone", "relevant_product_ids": ["p1"], "case_scope": "in_catalog_exact", "expected_categories": ["digital"]},
        {"id": "attr", "query": "iphone", "relevant_product_ids": ["p2"], "case_scope": "in_catalog_attribute_gap", "expected_categories": ["digital"]},
        {"id": "amb", "query": "gift", "relevant_product_ids": ["p3"], "case_scope": "in_catalog_ambiguous", "expected_categories": ["clothing"]},
        {
            "id": "gap",
            "query": "jacket",
            "relevant_product_ids": [],
            "acceptable_product_ids": [],
            "case_scope": "catalog_gap",
            "expected_behavior": "clarify",
            "allow_fallback": False,
            "expected_categories": ["clothing"],
        },
        {
            "id": "neg",
            "query": "medicine",
            "relevant_product_ids": [],
            "acceptable_product_ids": [],
            "case_scope": "negative_or_impossible",
            "expected_behavior": "clarify",
            "allow_fallback": False,
        },
    ]

    def fake_recommend(case):
        if case["id"] == "gap":
            return {"product_cards": [{"product_id": "p3"}]}, 1.0
        if case["id"] == "neg":
            return {"product_cards": []}, 1.0
        product_id = case["relevant_product_ids"][0]
        return {"product_cards": [{"product_id": product_id}]}, 1.0

    report = evaluate_cases(cases, top_ks=[5], catalog_by_id=catalog, recommend_fn=fake_recommend)
    assert report["main_summary"]["case_count"] == 2
    assert report["main_summary"]["hit@5"] == 1.0
    assert report["ambiguous_summary"]["case_count"] == 1
    assert report["catalog_gap_summary"]["catalog_gap_case_count"] == 1
    assert report["catalog_gap_summary"]["catalog_gap_violation_rate"] == 1.0
    assert report["negative_summary"]["negative_case_count"] == 1


def test_product_title_fallback_order():
    assert product_title(Product("p1", title="Title", name="Name")) == "Name"
    assert product_title({"product_id": "p2", "api_name": "API Name"}) == "API Name"
    assert product_title({"product_id": "p3"}) == "p3"


def test_miss_stage_guess_category_mismatch():
    catalog = {"p1": Product("p1", category="beauty")}
    guess = miss_stage_guess(
        case={"expected_categories": ["digital"], "relevant_product_ids": ["p2"]},
        ranked_ids=["p1"],
        metrics={"constraint_violation": 0},
        catalog_by_id=catalog,
        case_group="ecommerce",
    )
    assert guess == "category_routing_or_filter_error"


def test_miss_stage_guess_constraint_violation():
    catalog = {"p1": Product("p1", category="digital")}
    guess = miss_stage_guess(
        case={"expected_categories": ["digital"], "relevant_product_ids": ["p2"], "excluded_product_ids": ["p1"]},
        ranked_ids=["p1"],
        metrics={"constraint_violation": 1},
        catalog_by_id=catalog,
        case_group="ecommerce",
    )
    assert guess == "constraint_filter_error"


def test_miss_stage_guess_pc_route_not_used():
    catalog = {"p1": Product("p1", category="digital")}
    guess = miss_stage_guess(
        case={"expected_categories": ["pc_cpu"], "relevant_product_ids": ["pc_cpu_1"]},
        ranked_ids=["p1"],
        metrics={"constraint_violation": 0},
        catalog_by_id=catalog,
        case_group="pc",
    )
    assert guess == "pc_route_not_used"


def test_miss_stage_guess_ranking_error():
    catalog = {f"p{i}": Product(f"p{i}", category="digital") for i in range(1, 8)}
    guess = miss_stage_guess(
        case={"expected_categories": ["digital"], "relevant_product_ids": ["p6"]},
        ranked_ids=["p1", "p2", "p3", "p4", "p5", "p6"],
        metrics={"constraint_violation": 0},
        catalog_by_id=catalog,
        case_group="ecommerce",
    )
    assert guess == "ranking_error"


def test_pc_query_detection_examples():
    assert is_pc_query("1000元以内装机CPU，日常办公用")
    assert is_pc_query("3000元以内RTX 4060显卡")
    assert is_pc_query("32GB DDR5内存")
    assert is_pc_query("2TB高速固态硬盘")
    assert not is_pc_query("送女朋友护肤精华")


def test_parse_pc_part_constraints_examples():
    assert parse_pc_part_constraints("电脑升级2TB高速固态硬盘，适合游戏和剪辑")["storage_capacity"] == 2000
    memory = parse_pc_part_constraints("32GB内存，DDR5，适合新平台装机")
    assert memory["memory_capacity"] == 32
    assert memory["memory_type"] == "DDR5"
    gpu = parse_pc_part_constraints("3000元以内的游戏显卡，优先RTX 4060或4060 Ti")
    assert "RTX 4060" in gpu["gpu_chipset"]
    assert "RTX 4060 Ti" in gpu["gpu_chipset"]
    assert gpu["budget_max"] == 3000


def test_pc_query_uses_pc_parts_scope():
    result = recommend_shopping_products(
        "1000元以内装机CPU，日常办公用",
        use_llm=False,
        use_milvus_retrieval=False,
        catalog_scope="combined",
    )
    returned_ids = [card["product_id"] for card in result.product_cards[:3]]
    assert returned_ids
    assert all(product_id.startswith("pc_cpu_") for product_id in returned_ids)
    assert result.trace.get("catalog_scope") == "pc_parts"


def test_pc_storage_constraint_prioritizes_2tb_ssd():
    result = recommend_shopping_products(
        "电脑升级2TB高速固态硬盘，适合游戏和剪辑",
        use_llm=False,
        use_milvus_retrieval=False,
        catalog_scope="combined",
    )
    titles = [card["title"].lower() for card in result.product_cards[:5]]
    top3 = " ".join(titles[:3])
    assert any("2tb" in title for title in titles)
    assert "500gb" not in top3


def test_pc_memory_constraint_prioritizes_ddr5_32gb():
    result = recommend_shopping_products(
        "32GB内存，DDR5，适合新平台装机",
        use_llm=False,
        use_milvus_retrieval=False,
        catalog_scope="combined",
    )
    titles = [card["title"].lower() for card in result.product_cards[:5]]
    top3 = " ".join(titles[:3])
    assert any("ddr5" in title and "32gb" in title for title in titles)
    assert "ddr4" not in top3
    assert "16gb" not in top3


def test_pc_gpu_constraint_prioritizes_4060_family():
    result = recommend_shopping_products(
        "3000元以内的游戏显卡，优先RTX 4060或4060 Ti",
        use_llm=False,
        use_milvus_retrieval=False,
        catalog_scope="combined",
    )
    titles = [card["title"].lower() for card in result.product_cards[:5]]
    assert any("rtx 4060" in title for title in titles)


def test_pc_cpu_query_never_returns_ecommerce_products():
    result = recommend_shopping_products(
        "1000元以内装机CPU，日常办公用",
        use_llm=False,
        use_milvus_retrieval=False,
        catalog_scope="combined",
    )
    returned_ids = [card["product_id"] for card in result.product_cards[:5]]
    assert returned_ids
    assert all(product_id.startswith("pc_cpu_") for product_id in returned_ids)


def test_negative_fallback_blocks_unsupported_catalog_category():
    result = recommend_shopping_products(
        "我想买宠物猫粮和自动喂食器",
        use_llm=False,
        use_milvus_retrieval=False,
        catalog_scope="combined",
    )
    assert result.product_cards == []
    assert result.trace.get("no_match_reason")


def test_negative_fallback_blocks_impossible_budget():
    result = recommend_shopping_products(
        "500元以内买一台拍照旗舰手机",
        use_llm=False,
        use_milvus_retrieval=False,
        catalog_scope="combined",
    )
    assert result.product_cards == []
    assert result.trace.get("fallback_blocked_reason")


def test_product_type_filter_keeps_phone_query_from_earphones():
    query = "\u0033\u0030\u0030\u0030\u5143\u4ee5\u5185\u7684\u624b\u673a\uff0c\u9002\u5408\u5b66\u751f\u515a\u65e5\u5e38\u7528"
    result = recommend_shopping_products(
        query,
        use_llm=False,
        use_milvus_retrieval=False,
        catalog_scope="combined",
    )
    returned_ids = [card["product_id"] for card in result.product_cards[:3]]
    assert returned_ids == []
    assert result.trace["no_match_reason"] == "budget_catalog_gap"
    assert infer_product_type(query) == "phone"

def test_product_type_filter_keeps_laptop_query_from_tablets():
    result = recommend_shopping_products(
        "学生党笔记本，轻薄办公，不要苹果",
        use_llm=False,
        use_milvus_retrieval=False,
        catalog_scope="combined",
    )
    returned_ids = [card["product_id"] for card in result.product_cards[:3]]
    assert returned_ids
    assert not set(returned_ids) & {"p_digital_005", "p_digital_011", "p_digital_019", "p_digital_021", "p_digital_024", "p_digital_025"}


def test_product_type_filter_keeps_beverage_query_from_digital():
    result = recommend_shopping_products(
        "想买无糖饮料，夏天办公室囤货",
        use_llm=False,
        use_milvus_retrieval=False,
        catalog_scope="combined",
    )
    returned_ids = [card["product_id"] for card in result.product_cards[:3]]
    assert returned_ids
    assert all(product_id.startswith("p_food_") for product_id in returned_ids)


def test_product_type_filter_prioritizes_nuts_snack_gift():
    result = recommend_shopping_products(
        "送礼用的坚果零食礼盒，100元左右",
        use_llm=False,
        use_milvus_retrieval=False,
        catalog_scope="combined",
    )
    returned_ids = [card["product_id"] for card in result.product_cards[:3]]
    assert returned_ids[:2] == ["p_food_010", "p_food_009"]
    assert "p_food_018" not in returned_ids


def test_catalog_gap_jacket_returns_no_recommendation():
    result = recommend_shopping_products(
        "推荐户外防风外套",
        use_llm=False,
        use_milvus_retrieval=False,
        catalog_scope="combined",
    )
    assert result.product_cards == []
    assert result.trace.get("no_match_reason") == "missing_subcategory"
    assert result.trace.get("requested_product_type") == "jacket"
    assert "pants" in result.trace.get("available_neighbor_types", [])


def test_pc_motherboard_psu_case_and_4070ti_attributes():
    examples = [
        ("B760 DDR5 主板", "pc_motherboard_", ["b760", "ddr5"]),
        ("750W 金牌电源", "pc_psu_", ["750w"]),
        ("能装大显卡的机箱", "pc_case_", ["机箱"]),
        ("RTX 4070 Ti 显卡", "pc_gpu_", ["4070 ti"]),
    ]
    for query, prefix, terms in examples:
        result = recommend_shopping_products(
            query,
            use_llm=False,
            use_milvus_retrieval=False,
            catalog_scope="combined",
        )
        returned_ids = [card["product_id"] for card in result.product_cards[:5]]
        returned_titles = " ".join(card["title"].lower() for card in result.product_cards[:5])
        assert returned_ids
        assert all(product_id.startswith(prefix) for product_id in returned_ids)
        assert all(term in returned_titles for term in terms)


def test_ecommerce_subtype_preferences_for_apple_hat_and_base_makeup():
    checks = [
        ("送人的苹果手机，大屏一点", "p_digital_", ["apple", "iphone"]),
        ("夏天户外防晒帽", "p_clothes_", ["帽"]),
        ("想买控油持妆底妆", "p_beauty_", ["粉", "底妆"]),
    ]
    for query, prefix, terms in checks:
        result = recommend_shopping_products(
            query,
            use_llm=False,
            use_milvus_retrieval=False,
            catalog_scope="combined",
        )
        top_ids = [card["product_id"] for card in result.product_cards[:3]]
        top_titles = " ".join(card["title"].lower() for card in result.product_cards[:3])
        assert top_ids
        assert top_ids[0].startswith(prefix)
        assert any(term in top_titles for term in terms)
