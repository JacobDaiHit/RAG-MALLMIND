import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from scripts.eval_user_scenarios import main


EXPECTED_CASE_IDS = {
    "basic_oily_skin_sunscreen",
    "basic_under_200_sunscreen",
    "basic_pdf_example_cleanser",
    "basic_pdf_example_under_200_earphones",
    "intermediate_running_shoes_multiturn",
    "intermediate_compare_cream",
    "intermediate_clarify_phone",
    "advanced_negative_sunscreen",
    "advanced_sanya_bundle",
    "advanced_cart_crud",
    "advanced_photo_same_jacket",
}


def test_user_scenarios_eval_generates_reports(tmp_path):
    output_json = tmp_path / "user_scenarios_eval.json"
    output_md = tmp_path / "user_scenarios_eval.md"

    exit_code = main(["--output-json", str(output_json), "--output-md", str(output_md)])

    assert exit_code == 0
    assert output_json.is_file()
    assert output_md.is_file()

    report = json.loads(output_json.read_text(encoding="utf-8"))
    assert {"summary", "by_difficulty", "by_scenario_type", "cases", "catalog_summary", "failure_breakdown", "recommendations"} <= set(report)
    assert EXPECTED_CASE_IDS <= {case["case_id"] for case in report["cases"]}
    assert len({case["scenario_type"] for case in report["cases"]}) >= 9

    for case in report["cases"]:
        assert case.get("status") in {"ok", "failed", "suspicious", "not_applicable"}
        assert case.get("failure_type")
        if case["status"] == "failed":
            assert case.get("failure_reason")

    md = output_md.read_text(encoding="utf-8")
    assert "MallMind 典型用户场景评估报告" in md
    assert "failed case 明细" in md
