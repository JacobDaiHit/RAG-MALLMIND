from rag.api.recommendation_app import (
    build_requirement_questions,
    goal_with_attachment_context,
    normalize_attachments,
)
from rag.api.attachments import analyze_attachment_payloads
from rag.recommendation.cost_estimator import estimate_product_price
from rag.recommendation.product_loader import load_product_catalog, upsert_product
from rag.recommendation.recommendation_graph import stream_recommendation_graph
from rag.recommendation.recommendation_pipeline import parse_requirement_rule_based, recommend_api_stack
from rag.recommendation.input_preprocessor import build_preprocessed_goal, preprocess_user_input
from rag.schemas import ApiProduct, ComponentCategory, RequirementSpec


def test_catalog_loads_all_ecommerce_products():
    catalog = load_product_catalog(use_cache=False)
    counts = {}
    for product in catalog.products:
        counts[product.category] = counts.get(product.category, 0) + 1

    assert len(catalog.products) == 100
    assert counts[ComponentCategory.beauty] == 25
    assert counts[ComponentCategory.digital] == 25
    assert counts[ComponentCategory.clothing] == 25
    assert counts[ComponentCategory.food] == 25
    assert catalog.require("p_beauty_001").image_url.startswith("/product-images/")


def test_product_upsert_persists_new_and_existing_product(tmp_path):
    product_path = tmp_path / "products.json"
    product_path.write_text("[]", encoding="utf-8")

    product = ApiProduct(
        product_id="demo-product",
        title="Demo Product",
        brand="Demo Brand",
        category=ComponentCategory.digital,
        category_name="数码电子",
        sub_category="演示商品",
        base_price=199,
        min_price=199,
        max_price=299,
        image_url="/product-images/demo.jpg",
        description="用于测试商品库新增。",
        skus=[{"sku_id": "demo-sku", "price": 199, "properties": {"颜色": "黑色"}}],
    )

    catalog = upsert_product(product, product_path)
    assert catalog.require("demo-product").title == "Demo Product"

    updated = product.model_copy(update={"title": "Demo Product Updated"})
    catalog = upsert_product(updated, product_path)

    assert len(catalog.products) == 1
    assert catalog.require("demo-product").title == "Demo Product Updated"


def test_rule_parser_detects_budget_category_and_exclusions():
    requirement = parse_requirement_rule_based("200元以下的蓝牙耳机有哪些？不要白色")

    assert requirement.desired_categories == [ComponentCategory.digital]
    assert requirement.price_max == 200
    assert requirement.budget_level == "low"
    assert "白色" in requirement.excluded_terms


def test_bundle_recommendation_builds_cross_category_plan_without_llm():
    result = recommend_api_stack(
        "下周去三亚度假，帮我搭配一套从防晒到穿搭的方案，预算800以内",
        use_llm=False,
    )

    assert len(result.plans) == 1
    assert result.candidate_count == 100
    assert {component.role for component in result.plans[0].components} == {
        ComponentCategory.beauty,
        ComponentCategory.clothing,
    }
    assert result.plans[0].cost_estimate.total_price_max > 0
    assert result.trace["milvus_retrieval"]["status"] == "disabled"


def test_budget_gap_is_explained_when_no_exact_match():
    result = recommend_api_stack("200元以下的蓝牙耳机有哪些？", use_llm=False)

    assert "未找到严格满足 200 CNY 预算" in result.plans[0].summary
    assert any("200 CNY" in risk for risk in result.plans[0].risks)


def test_recommendation_graph_stream_reaches_done_without_milvus():
    events = list(
        stream_recommendation_graph(
            "推荐一款适合油皮的洗面奶，预算150以内",
            use_llm=False,
        )
    )

    event_names = [event.event for event in events]
    assert "plans" in event_names
    assert "guidance" in event_names
    assert event_names[-1] == "done"


def test_attachment_metadata_drives_multimodal_requirement_rules():
    attachments = normalize_attachments(
        [
            {
                "name": "street.jpg",
                "type": "image/jpeg",
                "size": 2048,
                "summary": "街拍照片，用户想找同款外套和相似穿搭。",
                "input_modalities": ["text", "image"],
            },
        ]
    )
    goal = goal_with_attachment_context("我想要同款外套，预算500以内", attachments)
    requirement = parse_requirement_rule_based(goal)

    assert "image" in requirement.input_modalities
    assert requirement.need_multimodal
    assert ComponentCategory.clothing in requirement.desired_categories


def test_attachment_normalization_accepts_images_only():
    attachments = normalize_attachments(
        [
            {"name": "street.jpg", "type": "image/jpeg", "size": 2048},
            {"name": "brief.pdf", "type": "application/pdf", "size": 4096},
        ]
    )

    assert len(attachments) == 1
    assert attachments[0]["name"] == "street.jpg"


def test_attachment_analysis_rejects_non_image_payloads():
    attachments = analyze_attachment_payloads(
        [
            {
                "name": "brief.pdf",
                "type": "application/pdf",
                "size": 32,
                "data_url": "data:application/pdf;base64,JVBERi0=",
            }
        ]
    )

    assert attachments[0]["analysis_status"] == "rejected"
    assert "只接收图片" in attachments[0]["summary"]


def test_vague_request_still_generates_followup_questions():
    requirement = parse_requirement_rule_based("帮我推荐商品")
    questions = build_requirement_questions(requirement, [])

    assert any("美妆护肤" in question for question in questions)
    assert any("预算" in question for question in questions)


def test_estimator_uses_sku_price_for_product_comparison():
    catalog = load_product_catalog(use_cache=False)
    requirement = RequirementSpec(
        raw_query="推荐一款防晒",
        desired_categories=[ComponentCategory.beauty],
        required_components=[ComponentCategory.beauty],
    )
    price, currency, assumptions = estimate_product_price(requirement, catalog.require("p_beauty_001"))

    assert price == catalog.require("p_beauty_001").min_price
    assert currency == "CNY"
    assert assumptions == []


def test_input_preprocessor_merges_audio_and_image_signals():
    attachments = [
        {
            "name": "voice.wav",
            "type": "audio/wav",
            "transcript": "想买黑色降噪耳机",
            "input_modalities": ["audio"],
        },
        {
            "name": "ref.jpg",
            "type": "image/jpeg",
            "summary": "图片中是黑色头戴式耳机",
            "input_modalities": ["image"],
        },
    ]

    prepared = preprocess_user_input("推荐一个耳机", attachments)
    merged = build_preprocessed_goal("推荐一个耳机", attachments)

    assert "audio" in prepared.modalities
    assert "image" in prepared.modalities
    assert "想买黑色降噪耳机" in merged
    assert "黑色头戴式耳机" in merged


def test_recommendation_result_exposes_router_cards_and_comparison_scope():
    result = recommend_api_stack("推荐一款500元以内的蓝牙耳机，不要白色，续航要久", use_llm=False)

    assert result.intent_route["route"] in {"single_product_recommendation", "condition_filter"}
    assert result.product_cards
    assert all(card["product_id"].startswith("p_") for card in result.product_cards)
    assert result.candidate_scope["active_filters"]["price_max"] == 500
    assert "白色" in result.candidate_scope["active_filters"]["excluded_terms"]
    assert result.comparison_table


def test_ecommerce_recommendation_excludes_pc_parts_by_default():
    result = recommend_api_stack("推荐一款适合学生党的手机，预算 3000 元以内", use_llm=False)

    assert result.trace["catalog_scope"] == "ecommerce"
    assert result.product_cards
    assert all(not card["product_id"].startswith("pc_") for card in result.product_cards)


def test_pc_parts_scope_returns_only_pc_part_cards():
    result = recommend_api_stack("推荐一款 RTX 4070 显卡", use_llm=False, catalog_scope="pc_parts")

    assert result.trace["catalog_scope"] == "pc_parts"
    assert result.trace["recommendation_domain"] == "single_pc_part"
    assert result.product_cards
    assert all(card["product_id"].startswith("pc_") for card in result.product_cards)
    assert all(card["category"].startswith("pc_") for card in result.product_cards)
