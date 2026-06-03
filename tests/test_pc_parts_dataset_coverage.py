import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PC_DATA = ROOT / "data" / "jd_pc_products"


def _products():
    payload = json.loads((PC_DATA / "products.json").read_text(encoding="utf-8"))
    return payload["products"]


def _by_type():
    rows = {}
    for product in _products():
        part_type = product["component_type"]
        part_type = {
            "pc_cpu": "cpu",
            "pc_gpu": "gpu",
            "pc_motherboard": "motherboard",
            "pc_memory": "memory",
            "pc_storage": "ssd",
            "pc_psu": "psu",
            "pc_case": "case",
            "pc_cooler": "cooler",
        }.get(part_type, part_type)
        rows.setdefault(part_type, []).append(product)
    return rows


def test_pc_parts_dataset_has_demo_sized_category_coverage():
    by_type = _by_type()
    assert len(_products()) >= 240
    assert len(by_type["cpu"]) >= 20
    assert len(by_type["gpu"]) >= 40
    assert len(by_type["motherboard"]) >= 40
    assert len(by_type["memory"]) >= 30
    assert len(by_type["ssd"]) >= 30
    assert len(by_type["psu"]) >= 30
    assert len(by_type["cooler"]) >= 25
    assert len(by_type["case"]) >= 25


def test_pc_parts_dataset_price_bands_cover_recommendation_ranges():
    by_type = _by_type()
    ranges = {
        "cpu": (400, 4500),
        "gpu": (0, 8000),
        "motherboard": (400, 2500),
        "memory": (150, 1200),
        "ssd": (200, 2000),
        "psu": (200, 1500),
        "cooler": (30, 800),
        "case": (100, 1000),
    }
    for part_type, (low, high) in ranges.items():
        prices = [product["price_cny"] for product in by_type[part_type]]
        assert min(prices) <= low + 100
        assert max(prices) >= high * 0.9


def test_pc_parts_dataset_has_required_compatibility_fields():
    required = {
        "motherboard": {"socket", "chipset", "memory_type", "form_factor", "m2_slots", "pcie_version", "wifi", "vrm_level"},
        "memory": {"memory_type", "capacity_gb"},
        "ssd": {"capacity_gb", "interface", "form_factor", "read_mb_s", "write_mb_s", "has_dram_cache", "nand_type"},
        "psu": {"wattage_w", "efficiency_rating", "modular", "pcie_8pin_connectors", "native_12vhpwr", "atx_version"},
        "cooler": {"tdp_w", "socket_support", "height_mm", "radiator_size_mm"},
        "case": {"motherboard_support", "gpu_clearance_mm", "cooler_clearance_mm", "max_psu_length_mm", "supported_radiator_sizes"},
    }
    for product in _products():
        part_type = product["component_type"]
        if part_type == "cpu_cooler":
            part_type = "cooler"
        needed = required.get(part_type)
        if not needed:
            continue
        specs = product["standardized_specs"]
        missing = [key for key in needed if key not in specs]
        assert missing == [], f"{product['id']} missing {missing}"


def test_pc_parts_dataset_has_no_image_or_screenshot_fields():
    offenders = []
    for product in _products():
        if "image_url" in product or "screenshot_path" in product or "screenshots" in product:
            offenders.append(product["id"])
        source = product.get("source", {})
        if any(key in source for key in ("image_url", "screenshot_path", "screenshots")):
            offenders.append(product["id"])
    assert offenders == []
