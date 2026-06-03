#!/usr/bin/env python3
"""Build a balanced PC parts demo dataset without product image dependencies.

The script is intentionally deterministic.  It produces a competition/demo sized
catalog that covers the price bands and compatibility fields needed by the PC
build recommender. Real JD URLs can be filled into the rows later; auditability
comes from structured fields and RAG text evidence, not generated images.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT / "data" / "jd_pc_products"
PIPELINE_PATH = ROOT / "data" / "jd_pc_parts_pipeline.py"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def slug(value: str) -> str:
    value = value.lower().replace("+", " plus")
    return re.sub(r"[^a-z0-9]+", "_", value).strip("_")


def load_pipeline_module():
    spec = importlib.util.spec_from_file_location("jd_pc_parts_pipeline", PIPELINE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load pipeline module: {PIPELINE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def product_id(category: str, brand: str, model: str) -> str:
    return f"pc_seed_{category}_{slug(brand + '_' + model)[:72]}"


def make_product(
    category: str,
    brand: str,
    model: str,
    price: int,
    specs: Dict[str, Any],
    *,
    title_suffix: str = "",
    tags: Iterable[str] = (),
    selling_points: Iterable[str] = (),
    limitations: Iterable[str] = (),
    recommendation: str = "",
) -> Dict[str, Any]:
    pid = product_id(category, brand, model)
    title = f"{brand} {model}{title_suffix}".strip()
    tag_text = "|".join(str(item) for item in tags if item)
    raw_specs_text = "；".join(f"{key} {value}" for key, value in specs.items() if value not in ("", None, []))
    return {
        "id": pid,
        "source": {
            "type": "curated_seed",
            "url": "scripts/build_pc_parts_dataset.py",
            "captured_at": utc_now_iso(),
            "source_note": "Curated seed row for balanced JD PC parts demo coverage; replace product_url with live JD item URL when available.",
        },
        "component_type": category,
        "component_name_zh": {
            "cpu": "处理器",
            "gpu": "显卡",
            "motherboard": "主板",
            "memory": "内存",
            "ssd": "固态硬盘",
            "psu": "电源",
            "cpu_cooler": "散热器",
            "case": "机箱",
        }.get(category, category),
        "title": title,
        "brand": brand,
        "model": model,
        "price_cny": float(price),
        "currency": "CNY",
        "shop": "JD demo seed",
        "sku": "",
        "product_url": "",
        "standardized_specs": specs,
        "advice_pc_fields": {
            "brand": brand,
            "model": model,
            "title": title,
            "price": str(price),
            "tags": tag_text,
            "selling_points": "|".join(selling_points),
            "limitations": "|".join(limitations),
            "recommendation_text": recommendation,
            "source.platform": "JD",
            "source.raw_specs_text": raw_specs_text,
        },
        "raw": {
            "brand": brand,
            "model": model,
            "title": title,
            "price": str(price),
            "platform": "JD",
            "category": category,
            "tags": tag_text,
            "selling_points": "|".join(selling_points),
            "limitations": "|".join(limitations),
            "recommendation_text": recommendation,
            "raw_specs_text": raw_specs_text,
        },
    }


def cpu_rows() -> List[Dict[str, Any]]:
    rows = [
        ("Intel", "Core i3-12100F", 449, "LGA1700", 4, 8, 58, False, "Alder Lake", "DDR4|DDR5"),
        ("AMD", "Ryzen 5 4500", 479, "AM4", 6, 12, 65, False, "Zen 2", "DDR4"),
        ("AMD", "Ryzen 5 5500", 599, "AM4", 6, 12, 65, False, "Zen 3", "DDR4"),
        ("AMD", "Ryzen 5 5600G", 699, "AM4", 6, 12, 65, True, "Zen 3", "DDR4"),
        ("Intel", "Core i5-12400F", 799, "LGA1700", 6, 12, 65, False, "Alder Lake", "DDR4|DDR5"),
        ("AMD", "Ryzen 5 5600", 829, "AM4", 6, 12, 65, False, "Zen 3", "DDR4"),
        ("AMD", "Ryzen 5 7500F", 999, "AM5", 6, 12, 65, False, "Zen 4", "DDR5"),
        ("Intel", "Core i5-13400F", 1199, "LGA1700", 10, 16, 65, False, "Raptor Lake", "DDR4|DDR5"),
        ("AMD", "Ryzen 5 7600", 1299, "AM5", 6, 12, 65, True, "Zen 4", "DDR5"),
        ("Intel", "Core i5-14400F", 1399, "LGA1700", 10, 16, 65, False, "Raptor Lake Refresh", "DDR4|DDR5"),
        ("AMD", "Ryzen 5 9600X", 1599, "AM5", 6, 12, 65, True, "Zen 5", "DDR5"),
        ("AMD", "Ryzen 7 7700", 1699, "AM5", 8, 16, 65, True, "Zen 4", "DDR5"),
        ("Intel", "Core i5-14600KF", 1799, "LGA1700", 14, 20, 125, False, "Raptor Lake Refresh", "DDR4|DDR5"),
        ("AMD", "Ryzen 7 9700X", 2299, "AM5", 8, 16, 65, True, "Zen 5", "DDR5"),
        ("Intel", "Core i7-14700F", 2499, "LGA1700", 20, 28, 65, False, "Raptor Lake Refresh", "DDR4|DDR5"),
        ("AMD", "Ryzen 9 7900", 2699, "AM5", 12, 24, 65, True, "Zen 4", "DDR5"),
        ("AMD", "Ryzen 7 7800X3D", 2999, "AM5", 8, 16, 120, True, "Zen 4 X3D", "DDR5"),
        ("Intel", "Core i7-14700KF", 3099, "LGA1700", 20, 28, 125, False, "Raptor Lake Refresh", "DDR4|DDR5"),
        ("AMD", "Ryzen 9 9900X", 3299, "AM5", 12, 24, 120, True, "Zen 5", "DDR5"),
        ("AMD", "Ryzen 9 7950X", 3999, "AM5", 16, 32, 170, True, "Zen 4", "DDR5"),
        ("Intel", "Core i9-14900KF", 4299, "LGA1700", 24, 32, 125, False, "Raptor Lake Refresh", "DDR4|DDR5"),
        ("AMD", "Ryzen 9 9950X", 4499, "AM5", 16, 32, 170, True, "Zen 5", "DDR5"),
    ]
    products = []
    for brand, model, price, socket, cores, threads, tdp, igpu, arch, memory in rows:
        tier = "高端生产力" if price >= 2500 else "中高端游戏/开发" if price >= 1500 else "主流游戏" if price >= 800 else "入门办公"
        products.append(make_product(
            "cpu", brand, model, price,
            {
                "brand": brand, "model": model, "socket": socket, "cores": cores, "threads": threads,
                "base_clock_ghz": 3.0, "boost_clock_ghz": 4.2 + min(cores, 16) / 12,
                "tdp_w": tdp, "integrated_graphics": igpu, "architecture": arch,
                "memory_type": memory.split("|")[-1], "platform": socket,
            },
            title_suffix=" 处理器",
            tags=[socket, memory, tier],
            selling_points=[tier, f"{cores}核{threads}线程", "平台覆盖均衡"],
            limitations=["需匹配主板接口", "高功耗型号需加强散热"],
            recommendation=f"适合{tier}价位段的装机方案。",
        ))
    return products


def gpu_rows() -> List[Dict[str, Any]]:
    base = [
        ("Integrated", "No discrete GPU / use CPU iGPU", 0, "iGPU", 0, "", 0, 0, 0, "核显方案"),
        ("ASRock", "Radeon RX 6500 XT 4GB", 899, "RX 6500 XT", 4, "GDDR6", 107, 400, 190, "入门独显"),
        ("MSI", "GeForce GTX 1650 D6 VENTUS", 999, "GTX 1650", 4, "GDDR6", 75, 300, 170, "入门独显"),
        ("Sapphire", "Radeon RX 6600 8G", 1399, "RX 6600", 8, "GDDR6", 132, 450, 240, "入门独显"),
        ("ASUS", "Dual GeForce RTX 4060 O8G", 2199, "RTX 4060", 8, "GDDR6", 115, 550, 227, "1080p游戏"),
        ("Sapphire", "Radeon RX 7600 8G", 2099, "RX 7600", 8, "GDDR6", 165, 550, 240, "1080p游戏"),
        ("XFX", "Radeon RX 7600 XT 16G", 2499, "RX 7600 XT", 16, "GDDR6", 190, 600, 280, "1080p游戏"),
        ("NVIDIA", "GeForce RTX 4060 Ti 8G", 2999, "RTX 4060 Ti", 8, "GDDR6", 160, 550, 244, "2K游戏/AI入门"),
        ("ZOTAC", "GeForce RTX 4060 Ti 16G Twin Edge", 3499, "RTX 4060 Ti", 16, "GDDR6", 165, 600, 225, "2K游戏/AI入门"),
        ("MAXSUN", "GeForce RTX 4070 iCraft OC12G", 4299, "RTX 4070", 12, "GDDR6X", 200, 650, 320, "2K游戏/AI入门"),
        ("Sapphire", "Radeon RX 7700 XT 12G", 3299, "RX 7700 XT", 12, "GDDR6", 245, 700, 280, "2K游戏/AI入门"),
        ("Sapphire", "Radeon RX 7800 XT 16G", 3699, "RX 7800 XT", 16, "GDDR6", 263, 700, 320, "2K游戏/AI入门"),
        ("ZOTAC", "GeForce RTX 4070 SUPER X-GAMING", 4699, "RTX 4070 SUPER", 12, "GDDR6X", 220, 700, 307, "高端游戏/AI"),
        ("Colorful", "GeForce RTX 4070 Ti SUPER 16G", 6299, "RTX 4070 Ti SUPER", 16, "GDDR6X", 285, 750, 330, "高端游戏/AI"),
        ("MSI", "GeForce RTX 4080 SUPER 16G", 7999, "RTX 4080 SUPER", 16, "GDDR6X", 320, 850, 336, "高端游戏/AI"),
        ("XFX", "Radeon RX 7900 XTX 24G", 6999, "RX 7900 XTX", 24, "GDDR6", 355, 850, 344, "高端游戏/AI"),
    ]
    variants = []
    for item in base:
        variants.append(item)
        brand, model, price, chip, vram, mem, tdp, psu, length, tier = item
        if price and len(variants) < 40:
            variants.append((brand, model + " OC", min(price + 180, 8000), chip, vram, mem, tdp + 10, psu, length + 8, tier))
    cycle_index = 0
    while len(variants) < 40:
        brand, model, price, chip, vram, mem, tdp, psu, length, tier = base[1 + cycle_index % (len(base) - 1)]
        variants.append((brand, f"{model} Edition {cycle_index + 1}", min(price + 90 * (cycle_index + 1), 8000), chip, vram, mem, tdp + 5, psu, length + 4, tier))
        cycle_index += 1
    products = []
    for brand, model, price, chip, vram, mem, tdp, psu, length, tier in variants[:40]:
        cuda = "RTX" in chip or "GTX" in chip
        products.append(make_product(
            "gpu", brand, model, price,
            {
                "brand": brand, "model": model, "chipset": chip, "vram_gb": vram,
                "memory_type": mem, "bus_width_bit": 256 if vram >= 16 else 192 if vram >= 12 else 128,
                "interface": "PCIe 4.0 x16", "length_mm": length, "power_w": tdp,
                "tdp_w": tdp, "recommended_psu_w": psu, "power_connector": "12VHPWR" if psu >= 700 and cuda else "8pin x 1",
                "cuda_support": cuda,
            },
            title_suffix=" 显卡",
            tags=[chip, f"{vram}GB" if vram else "核显", tier],
            selling_points=[tier, "显卡预算分层清晰", "适合按预算过滤"],
            limitations=["CUDA 工作流优先 NVIDIA" if not cuda and price else "需核对机箱限长"],
            recommendation=f"适合{tier}配置。",
        ))
    return products


def motherboard_rows() -> List[Dict[str, Any]]:
    seeds = [
        ("ASUS", "PRIME A520M-K", 449, "AM4", "A520", "M-ATX", "DDR4", 1, "PCIe 3.0", False, "entry"),
        ("MSI", "PRO H610M-E DDR4", 499, "LGA1700", "H610", "M-ATX", "DDR4", 1, "PCIe 4.0", False, "entry"),
        ("GIGABYTE", "B550M DS3H AC", 699, "AM4", "B550", "M-ATX", "DDR4", 2, "PCIe 4.0", True, "mainstream"),
        ("ASUS", "TUF GAMING B550M-PLUS WIFI II", 899, "AM4", "B550", "M-ATX", "DDR4", 2, "PCIe 4.0", True, "mainstream"),
        ("MSI", "PRO B650M-B", 799, "AM5", "B650", "M-ATX", "DDR5", 2, "PCIe 4.0", False, "mainstream"),
        ("MSI", "B650M MORTAR WIFI", 1299, "AM5", "B650", "M-ATX", "DDR5", 2, "PCIe 4.0", True, "mid"),
        ("ASUS", "TUF GAMING B650M-PLUS WIFI", 1399, "AM5", "B650", "M-ATX", "DDR5", 2, "PCIe 5.0", True, "mid"),
        ("GIGABYTE", "B760M AORUS ELITE AX DDR5", 1199, "LGA1700", "B760", "M-ATX", "DDR5", 2, "PCIe 4.0", True, "mainstream"),
        ("MSI", "PRO B760M-A WIFI DDR5", 1099, "LGA1700", "B760", "M-ATX", "DDR5", 2, "PCIe 4.0", True, "mainstream"),
        ("ASUS", "ROG STRIX B760-G GAMING WIFI", 1699, "LGA1700", "B760", "M-ATX", "DDR5", 3, "PCIe 5.0", True, "mid"),
        ("ASUS", "TUF GAMING X670E-PLUS WIFI", 2399, "AM5", "X670E", "ATX", "DDR5", 4, "PCIe 5.0", True, "high"),
        ("MSI", "MAG Z790 TOMAHAWK WIFI", 2299, "LGA1700", "Z790", "ATX", "DDR5", 4, "PCIe 5.0", True, "high"),
    ]
    products = []
    for index in range(40):
        brand, model, price, socket, chipset, form, mem, m2, pcie, wifi, level = seeds[index % len(seeds)]
        price = min(price + (index // len(seeds)) * 40, 2500)
        model = model if index < len(seeds) else f"{model} V{index // len(seeds) + 1}"
        products.append(make_product(
            "motherboard", brand, model, price,
            {
                "brand": brand, "model": model, "socket": socket, "chipset": chipset,
                "form_factor": form, "memory_type": mem, "memory_slots": 4 if "610" not in chipset and "520" not in chipset else 2,
                "max_memory_gb": 192 if mem == "DDR5" else 128, "m2_slots": m2,
                "pcie_version": pcie, "wifi": wifi, "bluetooth": wifi, "vrm_level": level,
            },
            title_suffix=" 主板",
            tags=[socket, chipset, mem, form, "Wi-Fi" if wifi else "no-wifi"],
            selling_points=["接口字段完整", "用于 CPU/内存/机箱兼容判断"],
            limitations=["需匹配 CPU 插槽与内存类型"],
            recommendation="适合按平台、芯片组和扩展能力筛选。",
        ))
    return products


def memory_rows() -> List[Dict[str, Any]]:
    seeds = [
        ("Kingbank", "Silver DDR4 16GB 3200", 189, "DDR4", 16, 2, 3200, "CL16", False),
        ("Kingston", "FURY Beast DDR4 32GB 3600", 399, "DDR4", 32, 2, 3600, "CL18", False),
        ("Crucial", "DDR5 16GB 5600", 299, "DDR5", 16, 1, 5600, "CL46", False),
        ("Kingston", "FURY Beast DDR5 32GB 6000", 699, "DDR5", 32, 2, 6000, "CL30", False),
        ("G.SKILL", "Trident Z5 RGB DDR5 32GB 6400", 899, "DDR5", 32, 2, 6400, "CL32", True),
        ("Corsair", "Vengeance DDR5 64GB 6000", 1099, "DDR5", 64, 2, 6000, "CL36", False),
    ]
    products = []
    for index in range(30):
        brand, model, price, mem, cap, modules, speed, latency, rgb = seeds[index % len(seeds)]
        model = model if index < len(seeds) else f"{model} Kit {index // len(seeds) + 1}"
        products.append(make_product(
            "memory", brand, model, min(price + (index // len(seeds)) * 20, 1200),
            {
                "brand": brand, "model": model, "memory_type": mem, "capacity_gb": cap,
                "modules": modules, "speed_mhz": speed, "latency": latency, "rgb": rgb,
            },
            title_suffix=" 内存套装",
            tags=[mem, f"{cap}GB", f"{speed}MHz"],
            selling_points=["明确 DDR4/DDR5", "容量覆盖开发和游戏需求"],
            limitations=["必须匹配主板内存类型"],
            recommendation="用于按容量、代际和频率筛选内存。",
        ))
    return products


def storage_rows() -> List[Dict[str, Any]]:
    seeds = [
        ("Kioxia", "EXCERIA G2 RC20 500GB", 249, 500, "PCIe 3.0 NVMe", 2100, 1700, True, "TLC"),
        ("ZHITAI", "TiPlus7100 1TB", 499, 1000, "PCIe 4.0 NVMe", 7000, 6000, False, "TLC"),
        ("WD", "Blue SN580 1TB", 399, 1000, "PCIe 4.0 NVMe", 4150, 4150, False, "TLC"),
        ("Samsung", "990 PRO 2TB", 1099, 2000, "PCIe 4.0 NVMe", 7450, 6900, True, "TLC"),
        ("WD", "BLACK SN850X 2TB", 999, 2000, "PCIe 4.0 NVMe", 7300, 6600, True, "TLC"),
        ("Crucial", "P3 Plus 4TB", 1899, 4000, "PCIe 4.0 NVMe", 5000, 4200, False, "QLC"),
    ]
    products = []
    for index in range(30):
        brand, model, price, cap, interface, read, write, dram, nand = seeds[index % len(seeds)]
        model = model if index < len(seeds) else f"{model} Rev{index // len(seeds) + 1}"
        products.append(make_product(
            "ssd", brand, model, min(price + (index // len(seeds)) * 25, 2000),
            {
                "brand": brand, "model": model, "storage_type": "SSD", "capacity_gb": cap,
                "interface": "M.2", "protocol": interface, "form_factor": "M.2 2280",
                "read_mb_s": read, "write_mb_s": write, "has_dram_cache": dram, "nand_type": nand,
            },
            title_suffix=" NVMe SSD",
            tags=[f"{cap}GB", "NVMe", interface],
            selling_points=["容量和速度字段完整", "区分 DRAM 与颗粒类型"],
            limitations=["高性能盘价格更高"],
            recommendation="用于区分普通盘和高性能系统盘。",
        ))
    return products


def psu_rows() -> List[Dict[str, Any]]:
    seeds = [
        ("Great Wall", "HOPE 5000DS 500W", 249, 500, "80Plus Bronze", "non_modular", False, "ATX 2.52", 2),
        ("Cooler Master", "MWE 550 Bronze", 329, 550, "80Plus Bronze", "non_modular", False, "ATX 2.52", 2),
        ("Huntkey", "WD650K 650W Gold", 449, 650, "80Plus Gold", "semi_modular", False, "ATX 2.52", 3),
        ("Super Flower", "LEADEX G 750W", 729, 750, "80Plus Gold", "full_modular", False, "ATX 2.52", 4),
        ("Great Wall", "GX850 ATX3.0", 699, 850, "80Plus Gold", "full_modular", True, "ATX 3.0", 4),
        ("Seasonic", "FOCUS GX-1000 ATX3", 1399, 1000, "80Plus Gold", "full_modular", True, "ATX 3.0", 5),
    ]
    products = []
    for index in range(30):
        brand, model, price, watt, rating, modular, hpwr, atx, pins = seeds[index % len(seeds)]
        model = model if index < len(seeds) else f"{model} V{index // len(seeds) + 1}"
        products.append(make_product(
            "psu", brand, model, min(price + (index // len(seeds)) * 25, 1500),
            {
                "brand": brand, "model": model, "wattage_w": watt,
                "efficiency_rating": rating, "modular": modular,
                "native_12vhpwr": hpwr, "has_12vhpwr": hpwr, "atx_version": atx,
                "pcie_8pin_connectors": pins, "length_mm": 150 if watt <= 850 else 160,
            },
            title_suffix=" 电源",
            tags=[f"{watt}W", rating, atx],
            selling_points=["功率和供电接口字段完整", "覆盖入门到高端"],
            limitations=["高端显卡优先 ATX3.0/12VHPWR"],
            recommendation="用于判断显卡供电、安全余量和机箱电源长度。",
        ))
    return products


def cooler_rows() -> List[Dict[str, Any]]:
    seeds = [
        ("AMD", "Wraith Stealth", 49, "air", 95, 54, 0, 1),
        ("Thermalright", "AX120 R SE", 89, "air", 150, 148, 0, 1),
        ("Thermalright", "Peerless Assassin 120", 199, "air", 220, 155, 0, 2),
        ("DeepCool", "AK620", 299, "air", 260, 160, 0, 2),
        ("Thermalright", "Frozen Magic 240", 399, "liquid", 250, 0, 240, 2),
        ("Cooler Master", "MasterLiquid 360", 699, "liquid", 300, 0, 360, 3),
    ]
    products = []
    sockets = ["AM4", "AM5", "LGA1700", "LGA1851"]
    for index in range(25):
        brand, model, price, ctype, cap, height, radiator, fans = seeds[index % len(seeds)]
        model = model if index < len(seeds) else f"{model} V{index // len(seeds) + 1}"
        products.append(make_product(
            "cpu_cooler", brand, model, min(price + (index // len(seeds)) * 15, 800),
            {
                "brand": brand, "model": model, "cooler_type": ctype,
                "socket_support": sockets, "tdp_w": cap, "cooling_capacity_w": cap,
                "height_mm": height, "radiator_size_mm": radiator, "fan_count": fans,
                "noise_db": 28 if price < 500 else 32, "rgb": price >= 399,
            },
            title_suffix=" CPU 散热器",
            tags=[ctype, f"{cap}W", f"{radiator}mm" if radiator else f"{height}mm"],
            selling_points=["散热能力和高度/冷排字段完整"],
            limitations=["塔式风冷需核对机箱限高；水冷需核对冷排位"],
            recommendation="用于按 CPU TDP、机箱限高和冷排支持匹配。",
        ))
    return products


def case_rows() -> List[Dict[str, Any]]:
    seeds = [
        ("SAMA", "Quzao Air M-ATX", 159, "M-ATX", ["M-ATX", "Mini-ITX"], 335, 165, 180, [240, 280]),
        ("JONSBO", "D31 MESH", 399, "M-ATX", ["M-ATX", "Mini-ITX"], 330, 168, 200, [240, 360]),
        ("LIAN LI", "A3-mATX", 499, "M-ATX", ["M-ATX", "Mini-ITX"], 415, 165, 220, [240, 360]),
        ("Phanteks", "P360A", 399, "ATX", ["ATX", "M-ATX", "Mini-ITX"], 400, 160, 220, [240, 280, 360]),
        ("NZXT", "H5 Flow", 599, "ATX", ["ATX", "M-ATX", "Mini-ITX"], 365, 165, 200, [240, 280]),
        ("Fractal Design", "North", 999, "ATX", ["ATX", "M-ATX", "Mini-ITX"], 355, 170, 255, [240, 360]),
    ]
    products = []
    for index in range(25):
        brand, model, price, size, boards, gpu_len, cooler_h, psu_len, radiators = seeds[index % len(seeds)]
        model = model if index < len(seeds) else f"{model} V{index // len(seeds) + 1}"
        products.append(make_product(
            "case", brand, model, min(price + (index // len(seeds)) * 18, 1000),
            {
                "brand": brand, "model": model, "case_size": size,
                "motherboard_support": boards, "form_factor_support": boards,
                "gpu_clearance_mm": gpu_len, "cooler_clearance_mm": cooler_h,
                "max_psu_length_mm": psu_len, "supported_radiator_sizes": radiators,
                "radiator_support": radiators, "fans_included": 2 if price >= 399 else 0,
                "max_fans": 8, "has_front_usb_c": price >= 399,
            },
            title_suffix=" 机箱",
            tags=[size, f"GPU{gpu_len}mm", f"风冷{cooler_h}mm"],
            selling_points=["机箱兼容字段完整", "覆盖入门到高端价位"],
            limitations=["需核对显卡长度、散热器高度和电源长度"],
            recommendation="用于避免显卡、风冷、水冷和主板尺寸不兼容。",
        ))
    return products


def build_products() -> List[Dict[str, Any]]:
    products = []
    for builder in (cpu_rows, gpu_rows, motherboard_rows, memory_rows, storage_rows, psu_rows, cooler_rows, case_rows):
        products.extend(builder())
    return products


def write_outputs(products: List[Dict[str, Any]], output_dir: Path) -> None:
    pipeline = load_pipeline_module()
    pipeline.write_products_json(products, output_dir / "products.json", field_spec_source="balanced_pc_seed_dataset")
    pipeline.write_parts_json(products, ROOT / "data" / "parts.json", field_spec_source="balanced_pc_seed_dataset")
    pipeline.write_classified_outputs(products, output_dir, field_spec_source="balanced_pc_seed_dataset")


def summarize(products: List[Dict[str, Any]]) -> Dict[str, Any]:
    summary: Dict[str, Any] = {"total": len(products), "by_type": {}}
    for product in products:
        key = "cooler" if product["component_type"] == "cpu_cooler" else product["component_type"]
        info = summary["by_type"].setdefault(key, {"count": 0, "min_price": None, "max_price": None})
        price = product["price_cny"]
        info["count"] += 1
        info["min_price"] = price if info["min_price"] is None else min(info["min_price"], price)
        info["max_price"] = price if info["max_price"] is None else max(info["max_price"], price)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a balanced PC parts dataset with structured compatibility fields.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    products = build_products()
    if not args.dry_run:
        write_outputs(products, args.output_dir)
    print(json.dumps(summarize(products), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
