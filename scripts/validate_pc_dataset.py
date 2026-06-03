"""Validate the local JD PC parts demo dataset."""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from rag.recommendation.pc_types import REQUIRED_PC_ROLES, base_model_key, normalize_pc_component_type


IMAGE_FIELDS = {"image_url", "screenshot_path", "screenshots", "image_path"}
DEFAULT_ROOT = ROOT_DIR / "data" / "jd_pc_products"
DEFAULT_REPORT_PATH = ROOT_DIR / "data" / "reports" / "pc_dataset_report.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate local PC parts products.json and parts.json files.")
    parser.add_argument("--strict", action="store_true", help="Exit 1 when validation errors are found; image residue is promoted to error.")
    parser.add_argument("--json", action="store_true", help="Only print JSON report.")
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="PC dataset root directory.")
    args = parser.parse_args()

    report = validate_pc_dataset(Path(args.root), strict=args.strict)
    DEFAULT_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        summary = report["summary"]
        print("PC dataset validation")
        print(f"- products: {summary['total_products']}")
        print(f"- parts: {summary['total_parts']}")
        print(f"- component_counts: {summary['component_counts']}")
        print(f"- errors: {summary['error_count']}")
        print(f"- warnings: {summary['warning_count']}")
        print(f"- report: {DEFAULT_REPORT_PATH.relative_to(ROOT_DIR)}")
    if args.strict and report["summary"]["error_count"]:
        raise SystemExit(1)


def validate_pc_dataset(root: Path = DEFAULT_ROOT, strict: bool = False) -> Dict[str, Any]:
    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    products: List[Dict[str, Any]] = []
    parts: List[Dict[str, Any]] = []
    seen_ids_by_file: Dict[str, Dict[str, str]] = defaultdict(dict)
    component_counts: Counter[str] = Counter()

    for path in sorted(root.glob("**/*.json")):
        if path.name not in {"products.json", "parts.json", "manifest.json"}:
            continue
        payload = _read_json(path, errors)
        if payload is None:
            continue
        if path.name == "manifest.json":
            _validate_manifest(path, payload, warnings)
            continue
        rows = _rows(payload, path.name)
        if path.name == "products.json":
            products.extend(rows)
        else:
            parts.extend(rows)
        for index, row in enumerate(rows):
            _validate_record(path, index, row, seen_ids_by_file[_relative(path)], component_counts, errors, warnings, strict)

    duplicates = find_duplicate_groups([*products, *parts])
    coverage = check_coverage([*products, *parts])
    summary = {
        "total_products": len(products),
        "total_parts": len(parts),
        "component_counts": dict(sorted(component_counts.items())),
        "error_count": len(errors),
        "warning_count": len(warnings),
    }
    return {
        "summary": summary,
        "errors": errors,
        "warnings": warnings,
        "duplicates": duplicates,
        "coverage": coverage,
    }


def _read_json(path: Path, errors: List[Dict[str, Any]]) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        errors.append(_issue(path, "json_parse", str(exc)))
        return None


def _rows(payload: Any, filename: str) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        key = "products" if filename == "products.json" else "parts"
        value = payload.get(key, [])
        return [item for item in value if isinstance(item, dict)]
    return []


def _validate_record(
    path: Path,
    index: int,
    row: Dict[str, Any],
    seen_ids: Dict[str, str],
    component_counts: Counter[str],
    errors: List[Dict[str, Any]],
    warnings: List[Dict[str, Any]],
    strict: bool,
) -> None:
    record_id = str(row.get("id") or row.get("product_id") or row.get("part_id") or "").strip()
    if not record_id:
        errors.append(_issue(path, "missing_id", f"record {index} has no id/part_id/product_id"))
    elif record_id in seen_ids:
        errors.append(_issue(path, "duplicate_id", f"{record_id} also appears in {seen_ids[record_id]}"))
    else:
        seen_ids[record_id] = _relative(path)

    raw_type = row.get("component_type") or row.get("part_type") or row.get("category")
    try:
        component_type = normalize_pc_component_type(raw_type)
        component_counts[component_type] += 1
    except ValueError:
        errors.append(_issue(path, "unknown_component_type", f"{record_id or index}: {raw_type!r}"))
        component_type = ""

    for key in ["title", "brand", "model", "currency"]:
        if not str(row.get(key) or "").strip():
            errors.append(_issue(path, "missing_required_field", f"{record_id}: missing {key}"))
    price = row.get("price_cny", row.get("price"))
    if not _is_number(price) or float(price) < 0:
        errors.append(_issue(path, "invalid_price", f"{record_id}: price must be a non-negative number"))
    elif float(price) == 0 and not _is_integrated_placeholder(row):
        errors.append(_issue(path, "zero_price", f"{record_id}: buildable PC part price should be greater than 0"))

    image_hits = _find_image_fields(row)
    if image_hits:
        target = errors if strict else warnings
        target.append(_issue(path, "pc_image_field_residue", f"{record_id}: remove PC image/screenshot fields {sorted(image_hits)}"))

    specs = row.get("standardized_specs") or row.get("specs") or {}
    _validate_specs(path, record_id, component_type, specs, errors)


def _validate_specs(path: Path, record_id: str, component_type: str, specs: Dict[str, Any], errors: List[Dict[str, Any]]) -> None:
    required_by_type = {
        "pc_cpu": ["socket", ["cores", "core_count"], ["threads", "thread_count"], "tdp_w", ["integrated_graphics", "has_integrated_gpu"]],
        "pc_motherboard": ["socket", "form_factor", "memory_type"],
        "pc_memory": ["memory_type", ["capacity_gb", "total_capacity_gb"], "speed_mhz"],
        "pc_gpu": [["length_mm"], ["power_w", "recommended_psu_w"]],
        "pc_psu": [["wattage_w", "wattage"]],
        "pc_case": [
            ["supported_motherboard_form_factors", "motherboard_support"],
            ["max_gpu_length_mm", "gpu_clearance_mm"],
            ["max_cpu_cooler_height_mm", "cooler_clearance_mm"],
            "max_psu_length_mm",
            ["supported_radiator_sizes", "radiator_support"],
        ],
        "pc_cooler": [["supported_sockets", "socket_support"], "cooler_type", ["cooling_capacity_w", "tdp_w"]],
    }
    for requirement in required_by_type.get(component_type, []):
        choices = requirement if isinstance(requirement, list) else [requirement]
        if not any(_present(specs.get(key)) for key in choices):
            errors.append(_issue(path, "missing_compatibility_field", f"{record_id}: missing one of {choices}"))
    if component_type == "pc_cooler":
        cooler_type = str(specs.get("cooler_type") or "").lower()
        if cooler_type == "air" and not _present(specs.get("height_mm")):
            errors.append(_issue(path, "missing_air_cooler_height", f"{record_id}: air cooler requires height_mm"))
        if cooler_type == "liquid" and not _present(specs.get("radiator_size_mm")):
            errors.append(_issue(path, "missing_liquid_radiator", f"{record_id}: liquid cooler requires radiator_size_mm"))


def _validate_manifest(path: Path, payload: Any, warnings: List[Dict[str, Any]]) -> None:
    text = json.dumps(payload, ensure_ascii=False)
    if re.search(r"[A-Za-z]:\\", text) or "\\data\\jd_pc_products\\" in text:
        warnings.append(_issue(path, "manifest_windows_path", "manifest contains historical Windows paths; runtime loaders must ignore them"))
    if "screenshot" in text.lower() or "pc-product-assets" in text:
        warnings.append(_issue(path, "manifest_screenshot_reference", "manifest still references screenshots/assets; do not use at runtime"))


def find_duplicate_groups(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        raw_type = row.get("component_type") or row.get("part_type") or row.get("category")
        try:
            component_type = normalize_pc_component_type(raw_type)
        except ValueError:
            continue
        key = base_model_key(row.get("brand"), component_type, row.get("model"), row.get("title"))
        groups[key].append(row)
    duplicates = []
    for key, items in groups.items():
        ids = [str(item.get("id") or item.get("part_id") or item.get("product_id")) for item in items]
        if len(set(ids)) > 1:
            duplicates.append({"base_model_key": key, "count": len(set(ids)), "ids": sorted(set(ids))})
    return sorted(duplicates, key=lambda item: -item["count"])[:80]


def check_coverage(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_type: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        try:
            by_type[normalize_pc_component_type(row.get("component_type") or row.get("part_type") or row.get("category"))].append(row)
        except ValueError:
            continue
    notes: List[str] = []
    role_ok = {role: bool(by_type.get(role)) for role in REQUIRED_PC_ROLES}
    if not all(role_ok.values()):
        notes.append("missing component roles: " + ", ".join(role for role, ok in role_ok.items() if not ok))

    def budget_possible(limit: float, require_gpu: bool = True, min_gpu_price: float = 0) -> bool:
        selected = {}
        for role in REQUIRED_PC_ROLES:
            candidates = by_type.get(role, [])
            if role == "pc_gpu":
                candidates = [item for item in candidates if float(item.get("price_cny", item.get("price", 0)) or 0) >= min_gpu_price]
                if not require_gpu:
                    candidates = by_type.get(role, [])
            if not candidates:
                notes.append(f"no candidates for {role}")
                return False
            selected[role] = min(candidates, key=lambda item: float(item.get("price_cny", item.get("price", 0)) or 0))
        return sum(float(item.get("price_cny", item.get("price", 0)) or 0) for item in selected.values()) <= limit

    coverage = {
        "can_build_office": budget_possible(3500, require_gpu=False),
        "can_build_mainstream_gaming": budget_possible(8000, min_gpu_price=1200),
        "can_build_high_end_gaming": budget_possible(13000, min_gpu_price=3000),
        "can_build_productivity": budget_possible(16000, min_gpu_price=2500),
        "notes": notes,
    }
    return coverage


def _find_image_fields(value: Any, prefix: str = "") -> set[str]:
    hits: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if key in IMAGE_FIELDS or "pc-product-assets" in str(child) or "screenshots" in str(child):
                hits.add(path)
            hits.update(_find_image_fields(child, path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            hits.update(_find_image_fields(child, f"{prefix}[{index}]"))
    return hits


def _is_number(value: Any) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def _present(value: Any) -> bool:
    return value not in (None, "", [])


def _is_integrated_placeholder(row: Dict[str, Any]) -> bool:
    text = " ".join(str(row.get(key) or "") for key in ["id", "part_id", "title", "model"]).lower()
    return "integrated" in text or "no discrete" in text


def _issue(path: Path, code: str, message: str) -> Dict[str, Any]:
    return {"file": _relative(path), "code": code, "message": message}


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT_DIR)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


if __name__ == "__main__":
    main()
