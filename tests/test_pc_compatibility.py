from rag.recommendation.pc_compatibility import check_pc_build_compatibility


def build(**overrides):
    parts = {
        "pc_cpu": {"role": "pc_cpu", "title": "CPU", "specs": {"socket": "AM5", "memory_type": "DDR5", "tdp_w": 65, "integrated_graphics": False}},
        "pc_motherboard": {"role": "pc_motherboard", "specs": {"socket": "AM5", "memory_type": "DDR5", "form_factor": "M-ATX", "wifi": True}},
        "pc_memory": {"role": "pc_memory", "specs": {"memory_type": "DDR5", "capacity_gb": 32, "speed_mhz": 6000}},
        "pc_gpu": {"role": "pc_gpu", "title": "RTX 4060", "specs": {"length_mm": 250, "power_w": 120, "recommended_psu_w": 550}},
        "pc_storage": {"role": "pc_storage", "specs": {"capacity_gb": 1000}},
        "pc_psu": {"role": "pc_psu", "specs": {"wattage_w": 650}},
        "pc_case": {"role": "pc_case", "specs": {"supported_motherboard_form_factors": ["M-ATX"], "max_gpu_length_mm": 330, "max_cpu_cooler_height_mm": 165, "max_psu_length_mm": 180, "supported_radiator_sizes": [240, 360]}},
        "pc_cooler": {"role": "pc_cooler", "specs": {"supported_sockets": ["AM5"], "cooler_type": "air", "cooling_capacity_w": 150, "height_mm": 155}},
    }
    for role, patch in overrides.items():
        parts[role]["specs"].update(patch)
    return parts


def assert_invalid(parts, check_name):
    report = check_pc_build_compatibility(parts)
    assert report["valid"] is False
    assert any(item["name"] == check_name and item["status"] == "fail" for item in report["checks"])


def test_socket_mismatch_invalid():
    assert_invalid(build(pc_motherboard={"socket": "LGA1700"}), "cpu_motherboard_socket")


def test_memory_type_mismatch_invalid():
    assert_invalid(build(pc_memory={"memory_type": "DDR4"}), "memory_type")


def test_gpu_too_long_invalid():
    assert_invalid(build(pc_gpu={"length_mm": 360}), "gpu_case_clearance")


def test_air_cooler_too_tall_invalid():
    assert_invalid(build(pc_cooler={"height_mm": 180}), "air_cooler_case_height")


def test_liquid_radiator_not_supported_invalid():
    assert_invalid(build(pc_cooler={"cooler_type": "liquid", "radiator_size_mm": 280}), "radiator_case_support")


def test_psu_insufficient_invalid():
    assert_invalid(build(pc_psu={"wattage_w": 450}), "psu_wattage")


def test_cpu_without_igpu_and_no_gpu_invalid():
    parts = build()
    parts["pc_gpu"] = {"role": "pc_gpu", "title": "Integrated no discrete GPU", "specs": {"length_mm": 0, "power_w": 0, "recommended_psu_w": 0}}
    assert_invalid(parts, "graphics_output")


def test_normal_gaming_build_valid():
    report = check_pc_build_compatibility(build())
    assert report["valid"] is True
    assert all(item["status"] in {"pass", "warning"} for item in report["checks"])
