"""Hard compatibility rules for catalog-selected PC component combinations.

``check_pc_build_compatibility`` verifies socket, memory, power, size, and
other structured constraints used by ``pc_build``. It returns explicit issues
instead of guessing replacements, and has no HTTP, LLM, or session dependency.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from rag.recommendation.pc_types import normalize_pc_component_type


def check_pc_build_compatibility(parts: Dict[str, Any], preferences: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Validate a selected PC build with deterministic structured checks."""

    preferences = preferences or {}
    by_role = {_role(key, value): value for key, value in (parts or {}).items()}
    errors: List[str] = []
    warnings: List[str] = []
    checks: List[Dict[str, str]] = []

    def add(name: str, passed: bool, detail: str, *, warning: bool = False) -> None:
        status = "pass" if passed else ("warning" if warning else "fail")
        checks.append({"name": name, "status": status, "detail": detail})
        if not passed and warning:
            warnings.append(detail)
        elif not passed:
            errors.append(detail)

    cpu = by_role.get("pc_cpu")
    motherboard = by_role.get("pc_motherboard")
    gpu = by_role.get("pc_gpu")
    memory = by_role.get("pc_memory")
    psu = by_role.get("pc_psu")
    case = by_role.get("pc_case")
    cooler = by_role.get("pc_cooler")

    missing = [role for role in ["pc_cpu", "pc_motherboard", "pc_memory", "pc_storage", "pc_psu", "pc_case", "pc_cooler"] if role not in by_role]
    if missing:
        for role in missing:
            add(f"missing_{role}", False, f"Missing required component: {role}")

    cpu_socket = _upper(_spec(cpu, "socket"))
    board_socket = _upper(_spec(motherboard, "socket"))
    add("cpu_motherboard_socket", bool(cpu_socket and cpu_socket == board_socket), f"CPU socket {cpu_socket or 'unknown'} vs motherboard socket {board_socket or 'unknown'}")

    cpu_memory = _upper(_spec(cpu, "memory_type"))
    board_memory = _upper(_spec(motherboard, "memory_type"))
    ram_memory = _upper(_spec(memory, "memory_type"))
    memory_ok = bool(board_memory and ram_memory == board_memory and (not cpu_memory or cpu_memory == board_memory))
    add("memory_type", memory_ok, f"CPU {cpu_memory or 'unknown'}, motherboard {board_memory or 'unknown'}, memory {ram_memory or 'unknown'}")

    board_factor = _upper(_spec(motherboard, "form_factor"))
    case_factors = [_upper(item) for item in _list_spec(case, "supported_motherboard_form_factors", "motherboard_support")]
    add("motherboard_case_form_factor", bool(board_factor and board_factor in case_factors), f"Motherboard {board_factor or 'unknown'} in case support {case_factors or ['unknown']}")

    gpu_length = _num(_spec(gpu, "length_mm"))
    case_gpu = _num(_spec(case, "max_gpu_length_mm", "gpu_clearance_mm"))
    if gpu and _is_integrated_gpu(gpu):
        add("gpu_case_clearance", True, "Integrated graphics placeholder does not need GPU clearance")
    else:
        add("gpu_case_clearance", bool(gpu_length and case_gpu and gpu_length <= case_gpu), f"GPU length {gpu_length:g}mm <= case clearance {case_gpu:g}mm" if gpu_length and case_gpu else "GPU length or case clearance is missing")

    cooler_type = str(_spec(cooler, "cooler_type") or "").lower()
    cooler_height = _num(_spec(cooler, "height_mm"))
    case_height = _num(_spec(case, "max_cpu_cooler_height_mm", "cooler_clearance_mm"))
    radiator = _num(_spec(cooler, "radiator_size_mm"))
    case_radiators = [_num(item) for item in _list_spec(case, "supported_radiator_sizes", "radiator_support")]
    if cooler_type == "liquid" or radiator:
        add("radiator_case_support", bool(radiator and radiator in case_radiators), f"Radiator {radiator:g}mm supported by case {case_radiators}" if radiator else "Radiator size is missing")
    else:
        add("air_cooler_case_height", bool(cooler_height and case_height and cooler_height <= case_height), f"Air cooler height {cooler_height:g}mm <= case limit {case_height:g}mm" if cooler_height and case_height else "Cooler height or case height is missing")

    cooler_sockets = [_upper(item) for item in _list_spec(cooler, "supported_sockets", "socket_support")]
    add("cooler_socket_support", bool(cpu_socket and cpu_socket in cooler_sockets), f"Cooler sockets {cooler_sockets or ['unknown']} include {cpu_socket or 'unknown'}")

    cpu_tdp = _num(_spec(cpu, "tdp_w"))
    cooling_capacity = _num(_spec(cooler, "cooling_capacity_w", "tdp_w"))
    required_cooling = cpu_tdp * 1.15 if cpu_tdp else 0
    add("cooler_capacity", bool(cooling_capacity and required_cooling and cooling_capacity >= required_cooling), f"Cooler {cooling_capacity:g}W >= CPU TDP margin {required_cooling:g}W" if cooling_capacity and required_cooling else "Cooling capacity or CPU TDP is missing")

    psu_watt = _num(_spec(psu, "wattage_w", "wattage"))
    gpu_recommended = _num(_spec(gpu, "recommended_psu_w"))
    gpu_power = _num(_spec(gpu, "power_w", "tdp_w"))
    estimated = (cpu_tdp or 0) + (gpu_power or 0) + 160
    required_psu = max(gpu_recommended or 0, estimated * 1.25 if estimated else 0)
    add("psu_wattage", bool(psu_watt and required_psu and psu_watt >= required_psu), f"PSU {psu_watt:g}W >= required {required_psu:g}W" if psu_watt and required_psu else "PSU wattage or power estimate is missing")

    has_igpu = bool(_spec(cpu, "integrated_graphics"))
    has_discrete = bool(gpu and not _is_integrated_gpu(gpu))
    add("graphics_output", has_igpu or has_discrete, "CPU has no integrated graphics and no discrete GPU is selected")

    if preferences.get("wifi"):
        board_wifi = bool(_spec(motherboard, "wifi"))
        add("wifi_preference", board_wifi, "User asked for Wi-Fi, selected motherboard does not include Wi-Fi", warning=True)

    return {"valid": not errors, "errors": errors, "warnings": warnings, "checks": checks, "status": "pass" if not errors else "failed"}


def _role(key: object, value: Any) -> str:
    raw = key
    if isinstance(value, dict):
        raw = value.get("role") or value.get("component_type") or value.get("part_type") or key
    else:
        raw = getattr(value, "role", key)
    return normalize_pc_component_type(raw)


def _spec(part: Any, *names: str) -> Any:
    specs = getattr(part, "specs", None)
    if specs is None and isinstance(part, dict):
        specs = part.get("specs") or part.get("standardized_specs") or {}
    for name in names:
        if isinstance(specs, dict) and name in specs:
            return specs.get(name)
    return None


def _list_spec(part: Any, *names: str) -> List[Any]:
    value = _spec(part, *names)
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _upper(value: Any) -> str:
    return str(value or "").strip().upper()


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _is_integrated_gpu(gpu: Any) -> bool:
    title = str(getattr(gpu, "title", "") or (gpu.get("title") if isinstance(gpu, dict) else "")).lower()
    model = str(getattr(gpu, "model", "") or (gpu.get("model") if isinstance(gpu, dict) else "")).lower()
    return "integrated" in title or "no discrete" in title or "integrated" in model
