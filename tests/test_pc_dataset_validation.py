import json
import importlib.util
import shutil
import uuid
from pathlib import Path

from rag.recommendation.pc_types import base_model_key, normalize_pc_component_type


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("validate_pc_dataset", ROOT / "scripts" / "validate_pc_dataset.py")
validator = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(validator)
validate_pc_dataset = validator.validate_pc_dataset


def test_pc_dataset_validation_strict_has_no_errors():
    report = validate_pc_dataset(ROOT / "data" / "jd_pc_products", strict=True)
    assert report["summary"]["error_count"] == 0
    assert report["summary"]["total_products"] > 0
    assert report["summary"]["component_counts"]["pc_gpu"] > 0


def test_pc_component_type_mapping():
    assert normalize_pc_component_type("cpu") == "pc_cpu"
    assert normalize_pc_component_type("ssd") == "pc_storage"
    assert normalize_pc_component_type("cpu_cooler") == "pc_cooler"


def _workspace_tmp_root():
    root = ROOT / ".pytest_pc_validation_tmp" / uuid.uuid4().hex
    root.mkdir(parents=True)
    return root


def test_validator_reports_missing_required_field():
    root = _workspace_tmp_root()
    try:
        (root / "products.json").write_text(json.dumps([{"id": "x", "component_type": "cpu"}]), encoding="utf-8")
        report = validate_pc_dataset(root, strict=True)
        codes = {item["code"] for item in report["errors"]}
        assert "missing_required_field" in codes
    finally:
        shutil.rmtree(root.parent, ignore_errors=True)


def test_validator_reports_pc_image_residue_in_strict():
    root = _workspace_tmp_root()
    try:
        row = {
            "id": "cpu_demo",
            "component_type": "cpu",
            "title": "CPU",
            "brand": "Demo",
            "model": "CPU",
            "price_cny": 1,
            "currency": "CNY",
            "image_url": "/pc-product-assets/x.svg",
            "standardized_specs": {"socket": "AM5", "cores": 6, "threads": 12, "tdp_w": 65, "integrated_graphics": True},
        }
        (root / "products.json").write_text(json.dumps([row]), encoding="utf-8")
        report = validate_pc_dataset(root, strict=True)
        assert any(item["code"] == "pc_image_field_residue" for item in report["errors"])
    finally:
        shutil.rmtree(root.parent, ignore_errors=True)


def test_base_model_key_groups_v2_v3():
    assert base_model_key("Brand", "gpu", "RTX 4060 V2") == base_model_key("brand", "pc_gpu", "rtx 4060 v3")
