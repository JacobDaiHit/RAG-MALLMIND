from rag.recommendation.pc_build import generate_pc_build_plan, load_pc_parts
from fastapi.testclient import TestClient
from rag.api.recommendation_app import app


client = TestClient(app)


def test_7000_white_quiet_gaming_build_returns_complete_plan():
    plan = generate_pc_build_plan(7000, usage=["游戏"], preferences={"color": "白色", "noise": "低噪音", "budget_strict": True})
    assert plan["compatibility"]["valid"] is True
    assert len(plan["parts"]) == 8
    assert plan["total_price"] <= 7000
    assert all("image_url" not in item and "screenshot_path" not in item for item in plan["parts"])
    assert plan["trace"]["structured_compatibility_validation_applied"] is True


def test_lower_budget_followup_shape():
    first = generate_pc_build_plan(7000, usage=["游戏"], preferences={"budget_strict": True})
    second = generate_pc_build_plan(6500, usage=["游戏"], preferences={"budget_strict": True}, previous_plan=first)
    assert second["total_price"] <= 6500
    assert second["comparison"]["baseline_total"] == first["total_price"]


def test_stronger_gpu_revalidates_whole_build():
    plan = generate_pc_build_plan(9000, usage=["游戏"], preferences={"gpu_priority": "stronger", "budget_strict": True})
    assert plan["compatibility"]["valid"] is True
    assert any(item["role"] == "pc_gpu" for item in plan["parts"])


def test_recommended_parts_are_from_local_loader_and_total_matches():
    local_ids = {part.product_id: part.price for part in load_pc_parts()}
    plan = generate_pc_build_plan(7000, usage=["游戏"], preferences={"budget_strict": True})
    prices = []
    for item in plan["parts"]:
        assert item["product_id"] in local_ids
        assert item["price"] == local_ids[item["product_id"]]
        prices.append(item["price"])
    assert plan["total_price"] == round(sum(prices), 2)


def test_chat_stream_real_chinese_pc_request_returns_plan():
    message = "\u5e2e\u6211\u914d\u4e00\u53f0 7000 \u5143\u4ee5\u5185\u7684\u6e38\u620f\u7535\u8111\uff0c\u767d\u8272\u3001\u4f4e\u566a\u97f3\u3002"
    response = client.post("/api/chat/stream", json={"session_id": "test-real-chinese-pc", "message": message, "images": []})
    assert response.status_code == 200
    assert "event: pc_build_plan" in response.text
    assert "event: done" in response.text
