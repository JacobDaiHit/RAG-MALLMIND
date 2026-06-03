import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from rag.recommendation import recommend_shopping_bundle  # noqa: E402


def main() -> int:
    """命令行入口函数，解析参数并调度当前脚本的主要流程。"""
    parser = argparse.ArgumentParser(description="Recommend three ecommerce shopping plans for a shopping goal.")
    parser.add_argument("goal", nargs="*", help="Shopping goal, for example: 推荐一款适合油皮的洗面奶...")
    parser.add_argument("--llm", action="store_true", help="Use configured LLM to enhance intent parsing and guidance.")
    args = parser.parse_args()

    goal = " ".join(args.goal).strip()
    if not goal:
        print("用法：python scripts\\recommend_api_stack.py \"下周去三亚度假，帮我搭配一套从防晒到穿搭的方案，预算800以内\"")
        return 2

    result = recommend_shopping_bundle(goal, use_llm=args.llm)
    requirement = result.requirement

    print("需求解析")
    print(f"- 场景: {requirement.scenario}")
    print(f"- 任务类型: {requirement.task_type}")
    print(f"- 目标类目: {', '.join(item.value for item in requirement.desired_categories)}")
    print(f"- 偏好: {', '.join(requirement.preferences) or '未指定'}")
    print(f"- 排除条件: {', '.join(requirement.excluded_terms) or '无'}")
    print(f"- 预算: {requirement.budget_level.value}")
    print(f"- 价格上限: {requirement.price_max or '未指定'}")
    if result.missing_fields:
        print(f"- 待补充字段: {', '.join(result.missing_fields)}")

    for plan in result.plans:
        cost = plan.cost_estimate
        print(f"\n== {plan.title} ==")
        print(plan.summary)
        print(f"总价: {cost.total_price_min:.2f} - {cost.total_price_max:.2f} {cost.currency}")

        print("\n商品")
        for component in plan.components:
            score = component.score.final_score if component.score else 0
            product = component.product
            print(
                f"- {component.role.value}: {product.title} ({product.product_id}) "
                f"{product.min_price:g}-{product.max_price:g} {product.currency}, score={score:.4f}"
            )

        print("\n优点")
        for item in plan.pros:
            print(f"- {item}")

        print("\n缺点")
        for item in plan.cons:
            print(f"- {item}")

        print("\n适用条件")
        for item in plan.suitable_for:
            print(f"- {item}")

        print("\n价格假设")
        for item in cost.assumptions[:6]:
            print(f"- {item}")

        print("\n评分表")
        for row in plan.score_table:
            print(
                f"- {row['role']} {row.get('product_id') or row.get('api_id')}: "
                f"final={row['final_score']:.4f}, "
                f"cost={row['cost_fit']:.2f}, "
                f"review={row['language_fit']:.2f}, "
                f"stock={row['latency_fit']:.2f}"
            )

        print("\n客户端展示步骤")
        for step in plan.architecture.integration_steps:
            print(f"- {step}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
