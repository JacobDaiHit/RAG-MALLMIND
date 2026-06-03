#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Slow, robots-aware JD public page / CSV / screenshot pipeline for PC parts.

Pipeline:
    public shop/search/product pages, CSV files, or product screenshots
        -> product information extraction
        -> field cleaning and standardization
        -> product JSON

The crawler is intentionally conservative:
    * honors robots.txt by default;
    * uses low page/item limits and long delays;
    * only reads public pages with GET requests;
    * never logs in, solves captchas, or bypasses access controls;
    * skips pages that look like login, captcha, or risk-control screens.

Examples:
    python data/jd_pc_parts_pipeline.py --url "https://search.jd.com/Search?keyword=RTX%204070"
    python data/jd_pc_parts_pipeline.py --csv data/input/pc_parts.csv
    python data/jd_pc_parts_pipeline.py --screenshot-dir data/input/screenshots
    python data/jd_pc_parts_pipeline.py --url "https://item.jd.com/100012043978.html" --capture-screenshots
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import logging
import os
import random
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib import parse, robotparser
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


LOGGER = logging.getLogger("jd_pc_parts_pipeline")

DEFAULT_USER_AGENT = (
    "trad-rag-jd-pc-parts-research/1.0 "
    "(slow; respects robots.txt; contact: local-research)"
)

DEFAULT_OUTPUT = Path("data/jd_pc_products/products.json")
DEFAULT_SCREENSHOT_OUTPUT = Path("data/jd_pc_products/screenshots")
DEFAULT_SEED_CSV = Path("data/jd_pc_products/source/core_pc_parts_seed.csv")
DEFAULT_DELAY_SECONDS = 8.0
DEFAULT_TIMEOUT_SECONDS = 25.0
DEFAULT_MAX_PAGES = 3
DEFAULT_MAX_ITEMS = 60

CAPTCHA_MARKERS = (
    "captcha",
    "验证",
    "安全验证",
    "滑块",
    "风险",
    "访问受限",
    "登录",
    "passport.jd.com",
)


@dataclass(frozen=True)
class PartSpec:
    """Extraction and normalization hints for one PC part family."""

    key: str
    zh_name: str
    keywords: Tuple[str, ...]
    jd_keywords: Tuple[str, ...]
    fields: Tuple[str, ...]


# The sixteen PC component families referenced by docs/advice_pc.md are modeled
# here as explicit extraction targets. Field names are stable snake_case keys so
# downstream RAG/import code can consume the JSON without Chinese label drift.
PART_SPECS: Tuple[PartSpec, ...] = (
    PartSpec(
        key="cpu",
        zh_name="处理器",
        keywords=("cpu", "处理器", "酷睿", "锐龙", "intel", "amd", "i3", "i5", "i7", "i9", "ryzen"),
        jd_keywords=("处理器", "CPU", "酷睿", "锐龙"),
        fields=(
            "brand",
            "model",
            "socket",
            "cores",
            "threads",
            "base_clock_ghz",
            "boost_clock_ghz",
            "tdp_w",
            "integrated_graphics",
            "generation",
        ),
    ),
    PartSpec(
        key="gpu",
        zh_name="显卡",
        keywords=("gpu", "显卡", "rtx", "gtx", "radeon", "rx ", "arc", "nvidia", "geforce"),
        jd_keywords=("显卡", "RTX", "Radeon"),
        fields=(
            "brand",
            "model",
            "chipset",
            "vram_gb",
            "memory_type",
            "bus_width_bit",
            "interface",
            "length_mm",
            "power_w",
            "recommended_psu_w",
        ),
    ),
    PartSpec(
        key="motherboard",
        zh_name="主板",
        keywords=("主板", "motherboard", "b650", "b760", "z790", "x670", "h610", "matx", "itx", "atx"),
        jd_keywords=("主板", "B650", "B760", "Z790"),
        fields=(
            "brand",
            "model",
            "socket",
            "chipset",
            "form_factor",
            "memory_type",
            "max_memory_gb",
            "m2_slots",
            "pcie_version",
            "wifi",
        ),
    ),
    PartSpec(
        key="memory",
        zh_name="内存",
        keywords=("内存", "ram", "ddr4", "ddr5", "频率", "套条"),
        jd_keywords=("内存 DDR5", "内存 DDR4"),
        fields=(
            "brand",
            "model",
            "capacity_gb",
            "modules",
            "memory_type",
            "speed_mhz",
            "latency",
            "voltage_v",
            "rgb",
        ),
    ),
    PartSpec(
        key="ssd",
        zh_name="固态硬盘",
        keywords=("固态", "ssd", "nvme", "m.2", "pcie4.0", "pcie5.0", "sata固态"),
        jd_keywords=("固态硬盘", "SSD NVMe", "M.2 SSD"),
        fields=(
            "brand",
            "model",
            "capacity_gb",
            "form_factor",
            "interface",
            "protocol",
            "read_mb_s",
            "write_mb_s",
            "endurance_tbw",
        ),
    ),
    PartSpec(
        key="hdd",
        zh_name="机械硬盘",
        keywords=("机械硬盘", "hdd", "sata硬盘", "7200转", "5400转"),
        jd_keywords=("机械硬盘", "HDD"),
        fields=(
            "brand",
            "model",
            "capacity_gb",
            "size_inch",
            "interface",
            "rpm",
            "cache_mb",
            "warranty_years",
        ),
    ),
    PartSpec(
        key="psu",
        zh_name="电源",
        keywords=("电源", "psu", "金牌", "白金", "铜牌", "atx3", "全模组", "半模组"),
        jd_keywords=("电脑电源", "ATX3.0 电源"),
        fields=(
            "brand",
            "model",
            "wattage_w",
            "efficiency_rating",
            "modular",
            "atx_version",
            "pcie_8pin_connectors",
            "native_12vhpwr",
            "warranty_years",
        ),
    ),
    PartSpec(
        key="case",
        zh_name="机箱",
        keywords=("机箱", "case", "中塔", "全塔", "matx机箱", "itx机箱", "海景房"),
        jd_keywords=("电脑机箱", "ATX 机箱"),
        fields=(
            "brand",
            "model",
            "case_size",
            "motherboard_support",
            "gpu_clearance_mm",
            "cooler_clearance_mm",
            "radiator_support_mm",
            "fans_included",
            "drive_bays",
        ),
    ),
    PartSpec(
        key="cpu_cooler",
        zh_name="散热器",
        keywords=("散热器", "水冷", "风冷", "aio", "冷排", "塔式", "下压式"),
        jd_keywords=("CPU 散热器", "水冷散热器", "风冷散热器"),
        fields=(
            "brand",
            "model",
            "cooler_type",
            "socket_support",
            "radiator_size_mm",
            "height_mm",
            "fan_size_mm",
            "tdp_w",
            "argb",
        ),
    ),
    PartSpec(
        key="case_fan",
        zh_name="机箱风扇",
        keywords=("机箱风扇", "散热风扇", "argb风扇", "pwm风扇", "12cm风扇", "14cm风扇"),
        jd_keywords=("机箱风扇", "PWM 风扇"),
        fields=(
            "brand",
            "model",
            "fan_size_mm",
            "rpm",
            "airflow_cfm",
            "noise_dba",
            "pwm",
            "argb",
            "pack_count",
        ),
    ),
    PartSpec(
        key="monitor",
        zh_name="显示器",
        keywords=("显示器", "monitor", "电竞屏", "刷新率", "ips", "oled", "va屏", "2k", "4k"),
        jd_keywords=("显示器", "电竞显示器", "4K 显示器"),
        fields=(
            "brand",
            "model",
            "size_inch",
            "resolution",
            "refresh_rate_hz",
            "panel_type",
            "response_ms",
            "color_gamut",
            "ports",
            "sync",
        ),
    ),
    PartSpec(
        key="keyboard",
        zh_name="键盘",
        keywords=("键盘", "keyboard", "机械键盘", "轴", "热插拔", "三模"),
        jd_keywords=("机械键盘", "游戏键盘"),
        fields=(
            "brand",
            "model",
            "connection",
            "layout",
            "switch_type",
            "backlight",
            "hot_swappable",
            "keycap_material",
        ),
    ),
    PartSpec(
        key="mouse",
        zh_name="鼠标",
        keywords=("鼠标", "mouse", "dpi", "传感器", "无线鼠标", "游戏鼠标"),
        jd_keywords=("游戏鼠标", "无线鼠标"),
        fields=(
            "brand",
            "model",
            "connection",
            "dpi",
            "weight_g",
            "sensor",
            "buttons",
            "battery_hours",
        ),
    ),
    PartSpec(
        key="headset",
        zh_name="耳机",
        keywords=("耳机", "耳麦", "headset", "headphone", "麦克风", "降噪", "7.1"),
        jd_keywords=("游戏耳机", "电脑耳机"),
        fields=(
            "brand",
            "model",
            "connection",
            "driver_mm",
            "impedance_ohm",
            "microphone",
            "surround",
            "weight_g",
        ),
    ),
    PartSpec(
        key="speaker",
        zh_name="音箱",
        keywords=("音箱", "音响", "speaker", "低音炮", "2.0", "2.1", "蓝牙音箱"),
        jd_keywords=("电脑音箱", "桌面音箱"),
        fields=(
            "brand",
            "model",
            "channels",
            "power_w",
            "connection",
            "frequency_response",
            "bluetooth_version",
        ),
    ),
    PartSpec(
        key="network_card",
        zh_name="网卡",
        keywords=("网卡", "wifi6", "wifi 6", "wifi7", "wifi 7", "蓝牙", "千兆", "万兆"),
        jd_keywords=("无线网卡", "WiFi7 网卡", "万兆网卡"),
        fields=(
            "brand",
            "model",
            "interface",
            "wireless_standard",
            "bluetooth_version",
            "speed_mbps",
            "antennas",
        ),
    ),
)

PART_SPEC_BY_KEY = {spec.key: spec for spec in PART_SPECS}
ACTIVE_PART_SPECS = PART_SPECS
ACTIVE_PART_SPEC_BY_KEY = PART_SPEC_BY_KEY
ACTIVE_ADVICE_FIELD_LABELS: Dict[str, Tuple[str, ...]] = {}

DOC_PART_TYPE_TO_COMPONENT = {
    "cpu": "cpu",
    "motherboard": "motherboard",
    "gpu": "gpu",
    "memory": "memory",
    "storage": "ssd",
    "psu": "psu",
    "cooler": "cpu_cooler",
    "case": "case",
}

COMPONENT_TO_DOC_PART_TYPE = {
    "cpu": "cpu",
    "motherboard": "motherboard",
    "gpu": "gpu",
    "memory": "memory",
    "ssd": "storage",
    "hdd": "storage",
    "psu": "psu",
    "cpu_cooler": "cooler",
    "case": "case",
}

CORE_DOC_PART_TYPES = ("cpu", "motherboard", "gpu", "memory", "storage", "psu", "cooler", "case")

SAFE_COMPONENT_SEED_URLS: Dict[str, Tuple[str, ...]] = {
    # Public JD item pages discovered from search-engine snippets. They are kept
    # query-free because JD's generic robots.txt rules disallow /*? paths.
    # The crawler still checks the target host's robots.txt before every fetch.
    "gpu": (
        "https://item.jd.com/100059277629.html",
        "https://item.jd.com/100053199702.html",
        "https://item.jd.com/100268714364.html",
        "https://item.jd.com/10136082840816.html",
        "https://item.jd.com/100172027926.html",
        "https://item.jd.com/10131528374592.html",
        "https://item.jd.com/10152570075847.html",
    ),
}

BRANDS = (
    "Intel",
    "AMD",
    "NVIDIA",
    "华硕",
    "ASUS",
    "ROG",
    "微星",
    "MSI",
    "技嘉",
    "GIGABYTE",
    "七彩虹",
    "Colorful",
    "影驰",
    "GALAX",
    "索泰",
    "ZOTAC",
    "蓝宝石",
    "Sapphire",
    "讯景",
    "XFX",
    "铭瑄",
    "MAXSUN",
    "昂达",
    "ONDA",
    "耕升",
    "Gainward",
    "金士顿",
    "Kingston",
    "海盗船",
    "Corsair",
    "芝奇",
    "G.SKILL",
    "英睿达",
    "Crucial",
    "三星",
    "Samsung",
    "西部数据",
    "WD",
    "铠侠",
    "Kioxia",
    "致态",
    "ZHITAI",
    "长江存储",
    "希捷",
    "Seagate",
    "东芝",
    "Toshiba",
    "长城",
    "航嘉",
    "Huntkey",
    "海韵",
    "Seasonic",
    "振华",
    "Super Flower",
    "酷冷至尊",
    "Cooler Master",
    "利民",
    "Thermalright",
    "九州风神",
    "DeepCool",
    "联想",
    "Lenovo",
    "戴尔",
    "Dell",
    "惠普",
    "HP",
    "AOC",
    "LG",
    "明基",
    "BenQ",
    "罗技",
    "Logitech",
    "雷蛇",
    "Razer",
    "樱桃",
    "Cherry",
    "达尔优",
    "雷柏",
    "Rapoo",
    "漫步者",
    "Edifier",
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = html.unescape(str(value))
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("\u3000", " ").replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_url(url: str, base_url: Optional[str] = None) -> str:
    url = clean_text(url)
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if base_url:
        return parse.urljoin(base_url, url)
    return url


def stable_id(*parts: str) -> str:
    joined = "|".join(part for part in parts if part)
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:16]


def parse_first_number(pattern: str, text: str, cast: Any = float) -> Optional[Any]:
    match = re.search(pattern, text, re.IGNORECASE)
    if not match:
        return None
    raw = match.group(1).replace(",", "")
    try:
        return cast(raw)
    except ValueError:
        return None


def to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    match = re.search(r"[-+]?\d+(?:\.\d+)?", str(value).replace(",", ""))
    return float(match.group(0)) if match else None


def to_int(value: Any) -> Optional[int]:
    number = to_float(value)
    return int(round(number)) if number is not None else None


def capacity_to_gb(value: Any) -> Optional[int]:
    text = clean_text(value).lower()
    if not text:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)\s*(tb|t|gb|g)\b", text, re.IGNORECASE)
    if not match:
        return None
    number = float(match.group(1))
    unit = match.group(2).lower()
    if unit in {"tb", "t"}:
        number *= 1024
    return int(round(number))


def normalize_price(value: Any) -> Optional[float]:
    text = clean_text(value)
    if not text:
        return None
    match = re.search(r"(?:￥|¥|CNY|RMB)?\s*(\d+(?:,\d{3})*(?:\.\d{1,2})?)", text, re.IGNORECASE)
    if not match:
        return None
    return float(match.group(1).replace(",", ""))


def extract_brand(text: str) -> str:
    folded = text.lower()
    for brand in BRANDS:
        if brand.lower() in folded:
            return brand
    return ""


def classify_part(text: str, fallback: str = "") -> str:
    folded = f" {clean_text(text).lower()} "
    exact = clean_text(text).lower().replace("-", "_").replace(" ", "_")
    exact_aliases = {
        "cooler": "cpu_cooler",
        "storage": "ssd",
    }
    if exact in PART_SPEC_BY_KEY:
        return exact
    if exact in exact_aliases:
        return exact_aliases[exact]
    if fallback in PART_SPEC_BY_KEY:
        return fallback
    best: Tuple[int, str] = (0, "")
    for spec in PART_SPECS:
        score = sum(1 for keyword in spec.keywords if keyword.lower() in folded)
        if score > best[0]:
            best = (score, spec.key)
    return best[1] or "unknown"


def has_risk_control(text: str) -> bool:
    folded = clean_text(text).lower()
    return any(marker.lower() in folded for marker in CAPTCHA_MARKERS)


class RobotsGate:
    def __init__(self, user_agent: str, strict: bool = True, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.user_agent = user_agent
        self.strict = strict
        self.timeout = timeout
        self._parsers: Dict[str, Optional[robotparser.RobotFileParser]] = {}

    def can_fetch(self, url: str) -> bool:
        parsed = parse.urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return False
        key = f"{parsed.scheme}://{parsed.netloc}"
        if key not in self._parsers:
            self._parsers[key] = self._load_parser(key)
        parser_obj = self._parsers[key]
        if parser_obj is None:
            return not self.strict
        try:
            return parser_obj.can_fetch(self.user_agent, url)
        except Exception as exc:  # pragma: no cover - urllib parser internals vary.
            LOGGER.warning("robots.txt check failed for %s: %s", url, exc)
            return not self.strict

    def _load_parser(self, origin: str) -> Optional[robotparser.RobotFileParser]:
        robots_url = parse.urljoin(origin, "/robots.txt")
        parser_obj = robotparser.RobotFileParser()
        parser_obj.set_url(robots_url)
        try:
            with urlopen(
                Request(robots_url, headers={"User-Agent": self.user_agent}),
                timeout=self.timeout,
            ) as response:
                body = response.read().decode("utf-8", errors="ignore").splitlines()
            parser_obj.parse(body)
            LOGGER.info("Loaded robots.txt: %s", robots_url)
            return parser_obj
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            LOGGER.warning("Could not load robots.txt from %s: %s", robots_url, exc)
            return None


class RateLimiter:
    def __init__(self, delay_seconds: float, jitter_seconds: float = 2.0) -> None:
        self.delay_seconds = max(0.0, delay_seconds)
        self.jitter_seconds = max(0.0, jitter_seconds)
        self._last_fetch_at = 0.0

    def wait(self) -> None:
        elapsed = time.time() - self._last_fetch_at
        target = self.delay_seconds + random.uniform(0.0, self.jitter_seconds)
        if elapsed < target:
            time.sleep(target - elapsed)
        self._last_fetch_at = time.time()


@dataclass
class RawProduct:
    source_type: str
    source_url: str = ""
    title: str = ""
    price: Any = None
    shop: str = ""
    sku: str = ""
    image_url: str = ""
    screenshot_path: str = ""
    category: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


class JdPublicCrawler:
    def __init__(
        self,
        user_agent: str,
        delay_seconds: float,
        strict_robots: bool,
        timeout_seconds: float,
    ) -> None:
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self.robots = RobotsGate(user_agent=user_agent, strict=strict_robots, timeout=timeout_seconds)
        self.rate_limiter = RateLimiter(delay_seconds=delay_seconds)

    def fetch_text(self, url: str) -> Optional[str]:
        url = normalize_url(url)
        if not self.robots.can_fetch(url):
            LOGGER.warning("Skipped by robots.txt: %s", url)
            return None
        self.rate_limiter.wait()
        LOGGER.info("Fetching public page: %s", url)
        request = Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
            },
            method="GET",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                content = response.read()
            text = content.decode(charset, errors="ignore")
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            LOGGER.warning("Fetch failed for %s: %s", url, exc)
            return None
        if has_risk_control(text):
            LOGGER.warning("Skipped possible login/captcha/risk-control page: %s", url)
            return None
        return text

    def collect(self, urls: Sequence[str], max_pages: int, max_items: int) -> List[RawProduct]:
        products: List[RawProduct] = []
        visited: set[str] = set()
        queue: List[str] = [normalize_url(url) for url in urls if url]

        while queue and len(visited) < max_pages and len(products) < max_items:
            url = queue.pop(0)
            if url in visited:
                continue
            visited.add(url)
            text = self.fetch_text(url)
            if not text:
                continue
            page_products, product_links = extract_products_from_html(text, base_url=url)
            if not page_products and is_jd_item_url(url):
                page_products = [extract_product_page(text, base_url=url)]
            for product in page_products:
                if not product.source_url:
                    product.source_url = url
                products.append(product)
                if len(products) >= max_items:
                    break
            for link in product_links:
                if len(visited) + len(queue) >= max_pages:
                    break
                if link not in visited and link not in queue:
                    queue.append(link)

        return products[:max_items]

    def capture_screenshots(self, products: List[RawProduct], output_dir: Path) -> None:
        try:
            from playwright.sync_api import sync_playwright  # type: ignore
        except ImportError:
            LOGGER.warning("playwright is not installed; screenshot capture skipped")
            return

        output_dir.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent=self.user_agent,
                locale="zh-CN",
                viewport={"width": 1365, "height": 900},
            )
            page = context.new_page()
            for product in products:
                if not product.source_url:
                    continue
                if not self.robots.can_fetch(product.source_url):
                    LOGGER.warning("Screenshot skipped by robots.txt: %s", product.source_url)
                    continue
                self.rate_limiter.wait()
                try:
                    page.goto(product.source_url, wait_until="domcontentloaded", timeout=int(self.timeout_seconds * 1000))
                    page.wait_for_timeout(2500)
                    body_text = page.locator("body").inner_text(timeout=3000)
                    if has_risk_control(body_text):
                        LOGGER.warning("Screenshot skipped on possible risk-control page: %s", product.source_url)
                        continue
                    filename = f"{product.sku or stable_id(product.source_url)}.png"
                    screenshot_path = output_dir / filename
                    page.screenshot(path=str(screenshot_path), full_page=True)
                    product.screenshot_path = str(screenshot_path)
                except Exception as exc:  # pragma: no cover - depends on browser runtime.
                    LOGGER.warning("Screenshot failed for %s: %s", product.source_url, exc)
            context.close()
            browser.close()


def is_jd_item_url(url: str) -> bool:
    parsed = parse.urlparse(url)
    return parsed.netloc.endswith("jd.com") and re.search(r"/\d+\.html", parsed.path) is not None


def build_jd_search_url(keyword: str) -> str:
    query = parse.urlencode({"keyword": keyword}, encoding="utf-8")
    return f"https://search.jd.com/Search?{query}"


def configure_advice_pc_fields(
    doc_path: Path,
    section_marker: str = "十六",
    disabled: bool = False,
) -> str:
    """Load component fields from docs/advice_pc.md section 16 when available."""

    global ACTIVE_PART_SPECS, ACTIVE_PART_SPEC_BY_KEY, ACTIVE_ADVICE_FIELD_LABELS
    ACTIVE_PART_SPECS = PART_SPECS
    ACTIVE_PART_SPEC_BY_KEY = PART_SPEC_BY_KEY
    ACTIVE_ADVICE_FIELD_LABELS = {}
    if disabled:
        return "builtin"
    if not doc_path.exists():
        LOGGER.warning("Advice field doc not found, using builtin fields: %s", doc_path)
        return "builtin"
    try:
        doc_text = doc_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        doc_text = doc_path.read_text(encoding="utf-8-sig", errors="ignore")
    except OSError as exc:
        LOGGER.warning("Could not read advice field doc %s: %s", doc_path, exc)
        return "builtin"

    section_text = extract_advice_section(doc_text, section_marker=section_marker)
    label_map = parse_component_field_labels(section_text)
    if not label_map:
        LOGGER.warning("No component field labels parsed from %s section %s; using builtin fields", doc_path, section_marker)
        return "builtin"

    merged_specs: List[PartSpec] = []
    for spec in PART_SPECS:
        labels = tuple(label for label in label_map.get(spec.key, ()) if label)
        if not labels:
            merged_specs.append(spec)
            continue
        doc_fields = tuple(
            dict.fromkeys(canonicalize_advice_field_label(label, spec.key) for label in labels if label)
        )
        fields = tuple(dict.fromkeys((*doc_fields, *spec.fields)))
        merged_specs.append(
            PartSpec(
                key=spec.key,
                zh_name=spec.zh_name,
                keywords=spec.keywords,
                jd_keywords=spec.jd_keywords,
                fields=fields,
            )
        )
        ACTIVE_ADVICE_FIELD_LABELS[spec.key] = labels

    ACTIVE_PART_SPECS = tuple(merged_specs)
    ACTIVE_PART_SPEC_BY_KEY = {spec.key: spec for spec in ACTIVE_PART_SPECS}
    LOGGER.info(
        "Loaded advice_pc field labels for %s component families from %s",
        len(ACTIVE_ADVICE_FIELD_LABELS),
        doc_path,
    )
    return str(doc_path)


def extract_advice_section(doc_text: str, section_marker: str) -> str:
    lines = doc_text.splitlines()
    marker_patterns = (
        rf"^\s*#+\s*.*{re.escape(section_marker)}.*$",
        r"^\s*#+\s*.*(?:第)?十六(?:条|、|：|:|\s).*$",
        r"^\s*#+\s*.*(?:^|\D)16(?:\.|、|：|:|\s).*$",
        r"^\s*(?:16|十六)[\.、：:]\s*.*$",
    )
    start = -1
    start_level = 99
    for index, line in enumerate(lines):
        if any(re.search(pattern, line, re.I) for pattern in marker_patterns):
            start = index
            heading_match = re.match(r"^(\s*#+)", line)
            start_level = len(heading_match.group(1).strip()) if heading_match else 99
            break
    if start < 0:
        return doc_text

    end = len(lines)
    if start_level < 99:
        for index in range(start + 1, len(lines)):
            heading_match = re.match(r"^\s*(#+)\s+", lines[index])
            if heading_match and len(heading_match.group(1)) <= start_level:
                end = index
                break
    else:
        for index in range(start + 1, len(lines)):
            if re.match(r"^\s*(?:17|十七)[\.、：:]\s+", lines[index]):
                end = index
                break
    return "\n".join(lines[start:end])


def parse_component_field_labels(section_text: str) -> Dict[str, Tuple[str, ...]]:
    json_label_map = parse_component_field_labels_from_json_examples(section_text)
    if json_label_map:
        return json_label_map

    label_map: Dict[str, List[str]] = {spec.key: [] for spec in PART_SPECS}
    current_component = ""
    table_header: Optional[List[str]] = None

    for raw_line in section_text.splitlines():
        line = clean_text(raw_line.strip())
        if not line:
            continue

        if line.startswith("|") and line.endswith("|"):
            cells = [clean_text(cell) for cell in line.strip("|").split("|")]
            if cells and all(re.fullmatch(r":?-{2,}:?", cell.replace(" ", "")) for cell in cells):
                continue
            if table_header is None and any("字段" in cell or "配件" in cell or "类别" in cell for cell in cells):
                table_header = cells
                continue
            if table_header:
                component_cell, fields_cell = extract_component_table_cells(table_header, cells)
                component_key = identify_component(component_cell)
                if component_key:
                    label_map[component_key].extend(split_field_labels(fields_cell))
                continue

        heading_component = identify_component(line)
        if re.match(r"^\s*#+", raw_line) and heading_component:
            current_component = heading_component
            table_header = None
            continue

        colon_match = re.match(r"^[\-*\d\.\s]*([^：:]{1,24})[：:]\s*(.+)$", line)
        if colon_match:
            component_key = identify_component(colon_match.group(1))
            if component_key:
                current_component = component_key
                label_map[component_key].extend(split_field_labels(colon_match.group(2)))
                continue

        if current_component and re.match(r"^[\-*]\s+", raw_line):
            label_map[current_component].extend(split_field_labels(re.sub(r"^[\-*]\s+", "", line)))

    return {
        key: tuple(dict.fromkeys(label for label in labels if label and not is_noise_field_label(label)))
        for key, labels in label_map.items()
        if labels
    }


def parse_component_field_labels_from_json_examples(section_text: str) -> Dict[str, Tuple[str, ...]]:
    label_map: Dict[str, List[str]] = {spec.key: [] for spec in PART_SPECS}
    blocks = re.findall(r"```json\s*(.*?)```", section_text, flags=re.I | re.S)
    for block in blocks:
        try:
            loaded = json.loads(block)
        except json.JSONDecodeError:
            continue
        if not isinstance(loaded, dict):
            continue
        doc_part_type = clean_text(loaded.get("part_type", "")).lower()
        component = DOC_PART_TYPE_TO_COMPONENT.get(doc_part_type)
        if not component:
            component = identify_component(" ".join(clean_text(loaded.get(key, "")) for key in ("part_id", "title", "model")))
        if not component:
            continue
        labels = collect_json_field_paths(loaded)
        if doc_part_type == "storage":
            label_map["ssd"].extend(labels)
            label_map["hdd"].extend(labels)
        else:
            label_map[component].extend(labels)
    return {
        key: tuple(dict.fromkeys(labels))
        for key, labels in label_map.items()
        if labels
    }


def collect_json_field_paths(value: Any, prefix: str = "") -> List[str]:
    if isinstance(value, dict):
        paths: List[str] = []
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            paths.extend(collect_json_field_paths(child, path))
        return paths
    return [prefix] if prefix else []


def extract_component_table_cells(header: Sequence[str], cells: Sequence[str]) -> Tuple[str, str]:
    component_index = 0
    fields_index = len(cells) - 1
    for index, name in enumerate(header):
        if index >= len(cells):
            continue
        if any(token in name for token in ("配件", "类别", "品类", "组件", "部件")):
            component_index = index
        if "字段" in name or "参数" in name or "属性" in name:
            fields_index = index
    return cells[component_index] if component_index < len(cells) else "", cells[fields_index] if fields_index < len(cells) else ""


def identify_component(text: str) -> str:
    folded = clean_text(text).lower()
    for spec in PART_SPECS:
        names = (spec.key, spec.zh_name, *spec.keywords)
        if any(name and name.lower() in folded for name in names):
            return spec.key
    aliases = {
        "cpu": ("处理器", "中央处理器"),
        "gpu": ("显卡", "独立显卡", "图形卡"),
        "motherboard": ("主板",),
        "memory": ("内存", "内存条"),
        "ssd": ("固态", "固态硬盘"),
        "hdd": ("机械硬盘",),
        "psu": ("电源",),
        "case": ("机箱",),
        "cpu_cooler": ("散热器", "cpu散热", "水冷", "风冷"),
        "case_fan": ("机箱风扇", "风扇"),
        "monitor": ("显示器",),
        "keyboard": ("键盘",),
        "mouse": ("鼠标",),
        "headset": ("耳机", "耳麦"),
        "speaker": ("音箱", "音响"),
        "network_card": ("网卡",),
    }
    for key, names in aliases.items():
        if any(name.lower() in folded for name in names):
            return key
    return ""


def split_field_labels(text: str) -> List[str]:
    text = re.sub(r"[；;]", "，", clean_text(text))
    text = re.sub(r"\s*/\s*", "，", text)
    text = re.sub(r"\s+(?=[A-Za-z0-9\u4e00-\u9fff])", "，", text)
    labels = []
    for item in re.split(r"[，,、|]", text):
        item = clean_text(re.sub(r"^[\-*\d\.\s]+", "", item))
        item = re.sub(r"（.*?）|\(.*?\)", "", item).strip()
        if item:
            labels.append(item)
    return labels


def is_noise_field_label(label: str) -> bool:
    return label in {"字段", "建议字段", "补充字段", "参数", "属性", "说明", "备注"} or len(label) > 40


def canonicalize_advice_field_label(label: str, category: str) -> str:
    text = clean_text(label).lower()
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    dotted_aliases = {
        "core_count": "cores",
        "thread_count": "threads",
        "has_integrated_gpu": "integrated_graphics",
        "gpu_chip": "chipset",
        "vram_type": "memory_type",
        "memory_bus_bit": "bus_width_bit",
        "tdp_w": "tdp_w",
        "total_capacity_gb": "capacity_gb",
        "stick_count": "modules",
        "latency_cl": "latency",
        "read_speed_mb_s": "read_mb_s",
        "write_speed_mb_s": "write_mb_s",
        "wattage": "wattage_w",
        "modular_type": "modular",
        "pcie_8pin_count": "pcie_8pin_connectors",
        "supported_sockets": "socket_support",
        "cooling_capacity_w": "tdp_w",
        "case_type": "case_size",
        "supported_motherboard_form_factors": "motherboard_support",
        "max_gpu_length_mm": "gpu_clearance_mm",
        "max_cpu_cooler_height_mm": "cooler_clearance_mm",
        "included_fans": "fans_included",
    }
    if text in dotted_aliases:
        return dotted_aliases[text]
    compact = re.sub(r"[\s_\-：:（）()]+", "", text)
    category_specific = {
        ("gpu", "显存"): "vram_gb",
        ("gpu", "显存容量"): "vram_gb",
        ("gpu", "显存类型"): "memory_type",
        ("gpu", "位宽"): "bus_width_bit",
        ("gpu", "建议电源"): "recommended_psu_w",
        ("gpu", "供电"): "power_connector",
        ("case", "显卡限长"): "gpu_clearance_mm",
        ("case", "散热限高"): "cooler_clearance_mm",
        ("case", "主板兼容"): "motherboard_support",
        ("cpu_cooler", "散热类型"): "cooler_type",
        ("monitor", "尺寸"): "size_inch",
        ("monitor", "刷新率"): "refresh_rate_hz",
        ("network_card", "速率"): "speed_mbps",
    }
    for (field_category, token), key in category_specific.items():
        if category == field_category and token.lower() in compact:
            return key
    generic = (
        ("品牌", "brand"),
        ("brand", "brand"),
        ("型号", "model"),
        ("model", "model"),
        ("系列", "series"),
        ("芯片组", "chipset"),
        ("芯片", "chipset"),
        ("gpu核心", "chipset"),
        ("核心", "cores"),
        ("线程", "threads"),
        ("插槽", "socket"),
        ("接口", "interface"),
        ("规格", "form_factor"),
        ("板型", "form_factor"),
        ("容量", "capacity_gb"),
        ("内存类型", "memory_type"),
        ("频率", "speed_mhz"),
        ("时序", "latency"),
        ("电压", "voltage_v"),
        ("功耗", "power_w"),
        ("tdp", "tdp_w"),
        ("额定功率", "wattage_w"),
        ("认证", "efficiency_rating"),
        ("模组", "modular"),
        ("尺寸", "size_inch"),
        ("长度", "length_mm"),
        ("高度", "height_mm"),
        ("转速", "rpm"),
        ("缓存", "cache_mb"),
        ("读取", "read_mb_s"),
        ("写入", "write_mb_s"),
        ("分辨率", "resolution"),
        ("面板", "panel_type"),
        ("响应", "response_ms"),
        ("色域", "color_gamut"),
        ("连接", "connection"),
        ("轴体", "switch_type"),
        ("重量", "weight_g"),
        ("传感器", "sensor"),
        ("麦克风", "microphone"),
        ("声道", "channels"),
        ("蓝牙", "bluetooth_version"),
        ("无线标准", "wireless_standard"),
        ("保修", "warranty_years"),
        ("质保", "warranty_years"),
    )
    for token, key in generic:
        if token in compact:
            return key
    ascii_slug = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    if ascii_slug:
        return ascii_slug[:48]
    return f"advice_{stable_id(category, label)[:8]}"


def extract_json_ld(html_text: str) -> List[Dict[str, Any]]:
    blocks = re.findall(
        r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    values: List[Dict[str, Any]] = []
    for block in blocks:
        block = clean_text(block)
        if not block:
            continue
        try:
            loaded = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(loaded, dict):
            values.append(loaded)
        elif isinstance(loaded, list):
            values.extend(item for item in loaded if isinstance(item, dict))
    return values


def extract_product_links(html_text: str, base_url: str) -> List[str]:
    links = set()
    for raw in re.findall(r"""href=["']([^"']*item\.jd\.com/\d+\.html[^"']*)["']""", html_text, re.I):
        links.add(normalize_url(raw, base_url=base_url).split("?")[0])
    for sku in re.findall(r"""data-sku=["']?(\d{6,})["']?""", html_text, re.I):
        links.add(f"https://item.jd.com/{sku}.html")
    return sorted(links)


def extract_products_from_html(html_text: str, base_url: str) -> Tuple[List[RawProduct], List[str]]:
    products: List[RawProduct] = []
    links = extract_product_links(html_text, base_url=base_url)

    for product_json in extract_json_ld(html_text):
        type_value = str(product_json.get("@type", "")).lower()
        if "product" not in type_value and not product_json.get("name"):
            continue
        offers = product_json.get("offers") or {}
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        raw_image = product_json.get("image", "")
        if isinstance(raw_image, list):
            raw_image = raw_image[0] if raw_image else ""
        products.append(
            RawProduct(
                source_type="public_page",
                source_url=base_url,
                title=clean_text(product_json.get("name", "")),
                price=(offers or {}).get("price") if isinstance(offers, dict) else None,
                shop=clean_text(product_json.get("brand", {}).get("name", "") if isinstance(product_json.get("brand"), dict) else product_json.get("brand", "")),
                sku=clean_text(product_json.get("sku", "")),
                image_url=normalize_url(str(raw_image), base_url=base_url),
                raw={"json_ld": product_json},
            )
        )

    # JD listing pages usually render each item under li.gl-item with data-sku.
    item_blocks = re.findall(
        r"""<li[^>]+(?:class=["'][^"']*gl-item[^"']*["'][^>]*|data-sku=["']?\d+["']?[^>]*)>(.*?)</li>""",
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    for block in item_blocks:
        sku_match = re.search(r"""data-sku=["']?(\d{6,})["']?""", block, re.IGNORECASE)
        title_match = re.search(r"""<div[^>]+class=["'][^"']*p-name[^"']*["'][^>]*>(.*?)</div>""", block, re.I | re.S)
        price_match = re.search(r"""<div[^>]+class=["'][^"']*p-price[^"']*["'][^>]*>(.*?)</div>""", block, re.I | re.S)
        shop_match = re.search(r"""<div[^>]+class=["'][^"']*p-shop[^"']*["'][^>]*>(.*?)</div>""", block, re.I | re.S)
        img_match = re.search(r"""(?:src|data-lazy-img|source-data-lazy-img)=["']([^"']+)["']""", block, re.I)
        sku = sku_match.group(1) if sku_match else ""
        products.append(
            RawProduct(
                source_type="public_page",
                source_url=f"https://item.jd.com/{sku}.html" if sku else base_url,
                title=clean_text(title_match.group(1) if title_match else ""),
                price=clean_text(price_match.group(1) if price_match else ""),
                shop=clean_text(shop_match.group(1) if shop_match else ""),
                sku=sku,
                image_url=normalize_url(img_match.group(1), base_url=base_url) if img_match else "",
                raw={"html_block": clean_text(block)[:2000]},
            )
        )

    if not products and links:
        for link in links:
            sku_match = re.search(r"/(\d+)\.html", link)
            products.append(
                RawProduct(
                    source_type="public_page",
                    source_url=link,
                    sku=sku_match.group(1) if sku_match else "",
                    raw={"discovered_from": base_url},
                )
            )

    return products, links


def extract_product_page(html_text: str, base_url: str) -> RawProduct:
    title = ""
    title_patterns = (
        r"""<div[^>]+class=["'][^"']*sku-name[^"']*["'][^>]*>(.*?)</div>""",
        r"""<title>(.*?)</title>""",
        r"""<h1[^>]*>(.*?)</h1>""",
    )
    for pattern in title_patterns:
        match = re.search(pattern, html_text, flags=re.I | re.S)
        if match:
            title = clean_text(match.group(1))
            break
    sku_match = re.search(r"/(\d+)\.html", base_url)
    price_match = re.search(r"""(?:p-price|price)[^<]{0,80}(?:￥|¥)?\s*(\d+(?:\.\d{1,2})?)""", html_text, re.I)
    shop_match = re.search(r"""<a[^>]+class=["'][^"']*(?:name|shopName)[^"']*["'][^>]*>(.*?)</a>""", html_text, re.I | re.S)
    img_match = re.search(r"""(?:src|data-origin|data-lazy-img)=["']([^"']+\.(?:jpg|jpeg|png|webp)[^"']*)["']""", html_text, re.I)
    return RawProduct(
        source_type="public_page",
        source_url=base_url,
        title=title,
        price=price_match.group(1) if price_match else None,
        shop=clean_text(shop_match.group(1) if shop_match else ""),
        sku=sku_match.group(1) if sku_match else "",
        image_url=normalize_url(img_match.group(1), base_url=base_url) if img_match else "",
        raw={"page_excerpt": clean_text(html_text)[:3000]},
    )


def load_csv_products(csv_paths: Sequence[Path]) -> List[RawProduct]:
    products: List[RawProduct] = []
    for path in csv_paths:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                products.append(raw_product_from_mapping(row, source_type="csv", source_url=str(path)))
    return products


def load_component_seed_products(seed_csv: Path, components: Sequence[str]) -> List[RawProduct]:
    if not seed_csv.exists():
        LOGGER.warning("Seed CSV not found: %s", seed_csv)
        return []
    wanted = set(components)
    if "all" in wanted:
        wanted = set(PART_SPEC_BY_KEY)
    products = []
    with seed_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            category = classify_part(clean_text(row.get("category", "")) or clean_text(row.get("title", "")))
            doc_type = COMPONENT_TO_DOC_PART_TYPE.get(category, category)
            if category in wanted or doc_type in wanted:
                products.append(raw_product_from_mapping(row, source_type="csv_seed", source_url=str(seed_csv)))
    return products


def load_screenshot_products(screenshot_dir: Path, enable_ocr: bool = False) -> List[RawProduct]:
    products: List[RawProduct] = []
    if not screenshot_dir.exists():
        LOGGER.warning("Screenshot directory does not exist: %s", screenshot_dir)
        return products

    image_paths = sorted(
        path
        for path in screenshot_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
    )
    for image_path in image_paths:
        sidecar = load_sidecar_for_screenshot(image_path)
        ocr_text = run_optional_ocr(image_path) if enable_ocr else ""
        source_text = " ".join(
            part
            for part in (
                sidecar.get("title", ""),
                sidecar.get("raw_text", ""),
                image_path.stem.replace("_", " ").replace("-", " "),
                ocr_text,
            )
            if part
        )
        product = raw_product_from_mapping(sidecar, source_type="screenshot", source_url=str(image_path))
        if not product.title:
            product.title = source_text
        product.screenshot_path = str(image_path)
        product.raw["ocr_text"] = ocr_text
        products.append(product)
    return products


def load_sidecar_for_screenshot(image_path: Path) -> Dict[str, Any]:
    for suffix in (".json", ".txt"):
        sidecar = image_path.with_suffix(suffix)
        if not sidecar.exists():
            continue
        try:
            text = sidecar.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = sidecar.read_text(encoding="utf-8-sig", errors="ignore")
        if suffix == ".json":
            try:
                loaded = json.loads(text)
                return loaded if isinstance(loaded, dict) else {"raw_text": text}
            except json.JSONDecodeError:
                return {"raw_text": text}
        return {"raw_text": text, "title": text.splitlines()[0] if text.splitlines() else ""}
    return {}


def run_optional_ocr(image_path: Path) -> str:
    try:
        import pytesseract  # type: ignore
        from PIL import Image  # type: ignore
    except ImportError:
        LOGGER.warning("pytesseract/Pillow not installed; OCR skipped for %s", image_path)
        return ""
    try:
        return clean_text(pytesseract.image_to_string(Image.open(image_path), lang="chi_sim+eng"))
    except Exception as exc:  # pragma: no cover - depends on local OCR install.
        LOGGER.warning("OCR failed for %s: %s", image_path, exc)
        return ""


def raw_product_from_mapping(row: Dict[str, Any], source_type: str, source_url: str) -> RawProduct:
    aliases = {
        "title": ("title", "name", "product_name", "商品名称", "商品名", "标题"),
        "price": ("price", "price_cny", "jd_price", "价格", "京东价"),
        "shop": ("shop", "store", "vendor", "店铺", "店铺名", "商家"),
        "sku": ("sku", "jd_sku", "商品编号", "京东sku"),
        "url": ("url", "product_url", "link", "链接", "商品链接"),
        "image_url": ("image", "image_url", "img", "图片", "主图"),
        "category": ("category", "component_type", "part_type", "类别", "配件类型"),
        "brand": ("brand", "品牌"),
        "model": ("model", "型号"),
    }
    normalized = {clean_text(key).lower(): value for key, value in row.items()}

    def get_value(key: str) -> Any:
        for alias in aliases[key]:
            lookup = alias.lower()
            if lookup in normalized:
                return normalized[lookup]
        return ""

    product = RawProduct(
        source_type=source_type,
        source_url=clean_text(get_value("url")) or source_url,
        title=clean_text(get_value("title")),
        price=get_value("price"),
        shop=clean_text(get_value("shop")),
        sku=clean_text(get_value("sku")),
        image_url=normalize_url(clean_text(get_value("image_url"))),
        category=classify_part(clean_text(get_value("category"))),
        raw=dict(row),
    )
    if get_value("brand"):
        product.raw["brand"] = get_value("brand")
    if get_value("model"):
        product.raw["model"] = get_value("model")
    return product


def normalize_product(raw_product: RawProduct) -> Dict[str, Any]:
    text = " ".join(
        clean_text(value)
        for value in (
            raw_product.title,
            raw_product.shop,
            raw_product.raw.get("raw_text", ""),
            raw_product.raw.get("raw_specs_text", ""),
            raw_product.raw.get("tags", ""),
            raw_product.raw.get("selling_points", ""),
            raw_product.raw.get("limitations", ""),
            raw_product.raw.get("html_block", ""),
            raw_product.raw.get("page_excerpt", ""),
        )
        if value
    )
    category = classify_part(f"{raw_product.category} {text}", fallback=raw_product.category)
    spec = ACTIVE_PART_SPEC_BY_KEY.get(category)
    specs = extract_specs(category, text, raw_product.raw)
    advice_pc_fields = build_advice_pc_field_values(category, specs, text, raw_product.raw)
    brand = clean_text(raw_product.raw.get("brand", "")) or extract_brand(text)
    model = clean_text(raw_product.raw.get("model", "")) or extract_model(text, brand=brand, category=category)
    specs["brand"] = specs.get("brand") or brand
    specs["model"] = specs.get("model") or model
    product_url = normalize_url(raw_product.source_url)
    product_id = raw_product.sku or stable_id(product_url, raw_product.title, raw_product.screenshot_path)

    return {
        "id": product_id,
        "source": {
            "type": raw_product.source_type,
            "url": product_url,
            "captured_at": utc_now_iso(),
            "screenshot_path": raw_product.screenshot_path,
        },
        "component_type": category,
        "component_name_zh": spec.zh_name if spec else "未知",
        "title": clean_text(raw_product.title),
        "brand": brand,
        "model": model,
        "price_cny": normalize_price(raw_product.price),
        "currency": "CNY" if normalize_price(raw_product.price) is not None else "",
        "shop": clean_text(raw_product.shop),
        "sku": clean_text(raw_product.sku),
        "product_url": product_url if product_url.startswith(("http://", "https://")) else "",
        "image_url": normalize_url(raw_product.image_url),
        "standardized_specs": order_specs_for_category(category, specs),
        "advice_pc_fields": advice_pc_fields,
        "raw": raw_product.raw,
    }


def extract_model(text: str, brand: str, category: str) -> str:
    source = text
    if brand:
        source = re.sub(re.escape(brand), " ", source, flags=re.I)
    patterns = {
        "cpu": r"\b(?:i[3579]-?\d{4,5}[a-z]{0,3}|r[3579]\s*\d{4,5}[a-z]{0,3}|ryzen\s*[3579]\s*\d{4,5}[a-z]{0,3})\b",
        "gpu": r"\b(?:rtx|gtx|rx|arc)\s*\d{3,5}\s*(?:ti|super|xt|gre|oc)?\b",
        "motherboard": r"\b(?:b|h|z|x|a)\d{3}[a-z0-9\- ]{0,20}\b",
        "memory": r"\bddr[45]\s*\d{4,5}\b",
        "ssd": r"\b(?:sn\d{3,4}|p\d\s*plus|980\s*pro|990\s*pro|t\d{3,4})\b",
    }
    pattern = patterns.get(category, r"\b[A-Z0-9][A-Z0-9\-]{2,}\b")
    match = re.search(pattern, source, re.I)
    return clean_text(match.group(0).upper() if match else "")


def extract_specs(category: str, text: str, raw: Dict[str, Any]) -> Dict[str, Any]:
    specs: Dict[str, Any] = {}
    extract_common_specs(specs, text, raw)
    if category == "cpu":
        extract_cpu_specs(specs, text)
    elif category == "gpu":
        extract_gpu_specs(specs, text)
    elif category == "motherboard":
        extract_motherboard_specs(specs, text)
    elif category == "memory":
        extract_memory_specs(specs, text)
    elif category == "ssd":
        extract_ssd_specs(specs, text)
    elif category == "hdd":
        extract_hdd_specs(specs, text)
    elif category == "psu":
        extract_psu_specs(specs, text)
    elif category == "case":
        extract_case_specs(specs, text)
    elif category == "cpu_cooler":
        extract_cooler_specs(specs, text)
    elif category == "case_fan":
        extract_case_fan_specs(specs, text)
    elif category == "monitor":
        extract_monitor_specs(specs, text)
    elif category == "keyboard":
        extract_keyboard_specs(specs, text)
    elif category == "mouse":
        extract_mouse_specs(specs, text)
    elif category == "headset":
        extract_headset_specs(specs, text)
    elif category == "speaker":
        extract_speaker_specs(specs, text)
    elif category == "network_card":
        extract_network_card_specs(specs, text)
    extract_structured_raw_specs(specs, raw)
    if category == "gpu":
        specs.pop("capacity_gb", None)
        if specs.get("socket") and not re.match(r"^(?:AM[45]|LGA\d{4})$", clean_text(specs["socket"]), re.I):
            specs.pop("socket", None)
    return {key: value for key, value in specs.items() if value not in ("", None, [], {})}


def extract_common_specs(specs: Dict[str, Any], text: str, raw: Dict[str, Any]) -> None:
    extract_structured_raw_specs(specs, raw)
    extract_specs_from_raw_text(specs, text)
    for key, value in raw.items():
        normalized_key = clean_text(key).lower()
        if normalized_key in {"brand", "品牌"}:
            specs["brand"] = clean_text(value)
        elif normalized_key in {"model", "型号"}:
            specs["model"] = clean_text(value)
        elif normalized_key in {"warranty", "质保", "保修"}:
            specs["warranty_years"] = parse_first_number(r"(\d+(?:\.\d+)?)\s*年", clean_text(value))

    if (re.search(r"\b(?:A?RGB)\b", text, re.I) and not re.search(r"\b(?:A?RGB)\s*(?:false|否|无)", text, re.I)) or "灯效" in text:
        specs["rgb"] = True
    if "wifi" in text.lower() or "wi-fi" in text.lower() or "无线" in text:
        specs["wifi"] = True
    if "蓝牙" in text or "bluetooth" in text.lower():
        specs["bluetooth"] = True


def set_spec(specs: Dict[str, Any], key: str, value: Any) -> None:
    if value not in ("", None, [], {}) and specs.get(key) in ("", None, [], {}):
        specs[key] = value


def extract_specs_from_raw_text(specs: Dict[str, Any], text: str) -> None:
    socket = re_search_text(r"(?:CPU接口|处理器接口|接口|插槽)\s*(AM[45]|LGA\s?\d{4})", text)
    set_spec(specs, "socket", socket.replace(" ", "").upper())
    set_spec(specs, "cores", parse_first_number(r"(\d+)\s*核", text, int))
    set_spec(specs, "threads", parse_first_number(r"(\d+)\s*线程", text, int))
    set_spec(specs, "base_clock_ghz", parse_first_number(r"基础频率\s*(\d+(?:\.\d+)?)\s*GHz", text))
    set_spec(specs, "boost_clock_ghz", parse_first_number(r"(?:睿频|加速频率)\s*(\d+(?:\.\d+)?)\s*GHz", text))
    set_spec(specs, "tdp_w", parse_first_number(r"(?:TDP|散热能力)\s*(\d{2,4})\s*W", text, int))
    set_spec(specs, "integrated_graphics", parse_bool(re_search_text(r"核显\s*(true|false|是|否|有|无)", text)))
    set_spec(specs, "architecture", clean_text(re_search_text(r"架构\s*([^；;]+)", text)))
    set_spec(specs, "release_year", parse_first_number(r"(?:发布时间|发布年份)\s*(\d{4})", text, int))

    set_spec(specs, "chipset", clean_text(re_search_text(r"(?:芯片组|显卡型号)\s*([^；;]+)", text)))
    set_spec(specs, "form_factor", clean_text(re_search_text(r"板型\s*([^；;]+)", text)).upper())
    set_spec(specs, "memory_type", clean_text(re_search_text(r"(?:内存类型|显存类型)\s*([A-Za-z0-9]+)", text)).upper())
    set_spec(specs, "memory_slots", parse_first_number(r"内存插槽\s*(\d+)", text, int))
    set_spec(specs, "max_memory_gb", capacity_to_gb(re_search_text(r"最大内存\s*(\d+(?:\.\d+)?\s*(?:GB|G|TB|T))", text)))
    set_spec(specs, "max_memory_speed_mhz", parse_first_number(r"最高内存频率\s*(\d{4,5})\s*MHz", text, int))
    set_spec(specs, "m2_slots", parse_first_number(r"M\.?2\s*插槽\s*(\d+)", text, int))
    set_spec(specs, "sata_ports", parse_first_number(r"SATA(?:接口|端口)?\s*(\d+)", text, int))
    set_spec(specs, "wifi", parse_bool(re_search_text(r"Wi-?Fi\s*(true|false|是|否|有|无)", text, "")))
    set_spec(specs, "bluetooth", parse_bool(re_search_text(r"蓝牙\s*(true|false|是|否|有|无)", text, "")))

    set_spec(specs, "vram_gb", capacity_to_gb(re_search_text(r"显存容量\s*(\d+(?:\.\d+)?\s*(?:GB|G|TB|T))", text)))
    set_spec(specs, "bus_width_bit", parse_first_number(r"显存位宽\s*(\d{2,4})\s*bit", text, int))
    set_spec(specs, "recommended_psu_w", parse_first_number(r"建议电源\s*(\d{3,4})\s*W", text, int))
    set_spec(specs, "length_mm", parse_first_number(r"(?:显卡长度|长度)\s*(\d{2,4})\s*mm", text, int))
    set_spec(specs, "power_connector", clean_text(re_search_text(r"电源接口\s*([^；;]+)", text)))
    set_spec(specs, "cuda_support", parse_bool(re_search_text(r"CUDA\s*(true|false|是|否|有|无)", text, "")))

    capacity_source = re_search_text(r"(?:总容量|(?<!显存)容量)\s*(\d+(?:\.\d+)?\s*(?:GB|G|TB|T))", text)
    set_spec(specs, "capacity_gb", capacity_to_gb(capacity_source))
    set_spec(specs, "modules", parse_first_number(r"(?:条数|套条)\s*(\d+)", text, int))
    set_spec(specs, "speed_mhz", parse_first_number(r"频率\s*(\d{3,5})\s*MHz", text, int))
    latency = re_search_text(r"时序\s*CL?(\d{2,3})", text)
    set_spec(specs, "latency", f"CL{latency}" if latency else None)
    set_spec(specs, "rgb", parse_bool(re_search_text(r"RGB\s*(true|false|是|否|有|无)", text, "")))

    set_spec(specs, "storage_type", clean_text(re_search_text(r"存储类型\s*([^；;]+)", text)).upper())
    set_spec(specs, "interface", clean_text(re_search_text(r"接口\s*([^；;]+)", text)).upper())
    set_spec(specs, "protocol", clean_text(re_search_text(r"协议\s*([^；;]+)", text)))
    set_spec(specs, "form_factor", clean_text(re_search_text(r"规格\s*([^；;]+)", text)))
    set_spec(specs, "read_mb_s", parse_first_number(r"读取\s*(\d{3,5})\s*MB/s", text, int))
    set_spec(specs, "write_mb_s", parse_first_number(r"写入\s*(\d{3,5})\s*MB/s", text, int))
    set_spec(specs, "endurance_tbw", parse_first_number(r"寿命\s*(\d{2,5})\s*TBW", text, int))
    set_spec(specs, "has_dram_cache", parse_bool(re_search_text(r"DRAM缓存\s*(true|false|是|否|有|无)", text, "")))
    set_spec(specs, "rpm", parse_first_number(r"转速\s*(\d{4,5})\s*RPM", text, int))
    set_spec(specs, "cache_mb", parse_first_number(r"缓存\s*(\d{2,4})\s*MB", text, int))

    set_spec(specs, "wattage_w", parse_first_number(r"额定功率\s*(\d{3,4})\s*W", text, int))
    set_spec(specs, "efficiency_rating", clean_text(re_search_text(r"认证\s*([^；;]+)", text)))
    set_spec(specs, "modular", clean_text(re_search_text(r"(?:^|[；;])\s*模组\s*([^；;]+)", text)))
    set_spec(specs, "native_12vhpwr", parse_bool(re_search_text(r"12V-?2x6\s*(true|false|是|否|有|无)", text, "")))
    set_spec(specs, "pcie_8pin_connectors", parse_first_number(r"PCIe\s*8pin\s*(\d+)", text, int))
    set_spec(specs, "warranty_years", parse_first_number(r"质保\s*(\d+)\s*年", text, int))

    set_spec(specs, "cooler_type", clean_text(re_search_text(r"散热类型\s*([^；;]+)", text)))
    socket_text = clean_text(re_search_text(r"支持接口\s*([^；;]+)", text))
    set_spec(specs, "socket_support", split_list_value(socket_text))
    set_spec(specs, "height_mm", parse_first_number(r"高度\s*(\d{2,4})\s*mm", text, int))
    set_spec(specs, "radiator_size_mm", parse_first_number(r"冷排\s*(\d{3})\s*mm", text, int))
    set_spec(specs, "fan_count", parse_first_number(r"风扇数量\s*(\d+)", text, int))
    set_spec(specs, "noise_db", parse_first_number(r"噪音\s*(\d+(?:\.\d+)?)\s*dBA", text))

    set_spec(specs, "case_size", clean_text(re_search_text(r"机箱类型\s*([^；;]+)", text)).upper())
    board_text = clean_text(re_search_text(r"支持主板\s*([^；;]+)", text))
    set_spec(specs, "motherboard_support", split_list_value(board_text))
    set_spec(specs, "gpu_clearance_mm", parse_first_number(r"显卡限长\s*(\d{2,4})\s*mm", text, int))
    set_spec(specs, "cooler_clearance_mm", parse_first_number(r"散热限高\s*(\d{2,4})\s*mm", text, int))
    set_spec(specs, "max_psu_length_mm", parse_first_number(r"电源限长\s*(\d{2,4})\s*mm", text, int))
    radiator_text = clean_text(re_search_text(r"冷排支持\s*([^；;]+)", text))
    set_spec(specs, "supported_radiator_sizes", [to_int(item) for item in split_list_value(radiator_text) if to_int(item) is not None])
    set_spec(specs, "fans_included", parse_first_number(r"标配风扇\s*(\d+)", text, int))
    set_spec(specs, "max_fans", parse_first_number(r"最大风扇\s*(\d+)", text, int))
    set_spec(specs, "has_front_usb_c", parse_bool(re_search_text(r"前置USB-C\s*(true|false|是|否|有|无)", text, "")))


def extract_structured_raw_specs(specs: Dict[str, Any], raw: Dict[str, Any]) -> None:
    aliases = {
        "socket": "socket",
        "core_count": "cores",
        "cores": "cores",
        "thread_count": "threads",
        "threads": "threads",
        "base_clock_ghz": "base_clock_ghz",
        "boost_clock_ghz": "boost_clock_ghz",
        "tdp_w": "tdp_w",
        "has_integrated_gpu": "integrated_graphics",
        "architecture": "architecture",
        "release_year": "release_year",
        "chipset": "chipset",
        "gpu_chip": "chipset",
        "form_factor": "form_factor",
        "memory_type": "memory_type",
        "memory_slots": "memory_slots",
        "max_memory_gb": "max_memory_gb",
        "max_memory_speed_mhz": "max_memory_speed_mhz",
        "m2_slots": "m2_slots",
        "sata_ports": "sata_ports",
        "has_wifi": "wifi",
        "has_bluetooth": "bluetooth",
        "vram_gb": "vram_gb",
        "vram_type": "memory_type",
        "memory_bus_bit": "bus_width_bit",
        "recommended_psu_w": "recommended_psu_w",
        "length_mm": "length_mm",
        "power_connector": "power_connector",
        "cuda_support": "cuda_support",
        "total_capacity_gb": "capacity_gb",
        "capacity_gb": "capacity_gb",
        "stick_count": "modules",
        "modules": "modules",
        "speed_mhz": "speed_mhz",
        "latency_cl": "latency",
        "has_rgb": "rgb",
        "storage_type": "storage_type",
        "interface": "interface",
        "protocol": "protocol",
        "read_speed_mb_s": "read_mb_s",
        "write_speed_mb_s": "write_mb_s",
        "read_mb_s": "read_mb_s",
        "write_mb_s": "write_mb_s",
        "endurance_tbw": "endurance_tbw",
        "has_dram_cache": "has_dram_cache",
        "wattage": "wattage_w",
        "wattage_w": "wattage_w",
        "efficiency_rating": "efficiency_rating",
        "modular_type": "modular",
        "modular": "modular",
        "has_12vhpwr": "native_12vhpwr",
        "has_12v_2x6": "native_12vhpwr",
        "pcie_8pin_count": "pcie_8pin_connectors",
        "warranty_years": "warranty_years",
        "cooler_type": "cooler_type",
        "supported_sockets": "socket_support",
        "cooling_capacity_w": "tdp_w",
        "height_mm": "height_mm",
        "radiator_size_mm": "radiator_size_mm",
        "fan_count": "fan_count",
        "noise_db": "noise_db",
        "case_type": "case_size",
        "supported_motherboard_form_factors": "motherboard_support",
        "max_gpu_length_mm": "gpu_clearance_mm",
        "max_cpu_cooler_height_mm": "cooler_clearance_mm",
        "max_psu_length_mm": "max_psu_length_mm",
        "supported_radiator_sizes": "supported_radiator_sizes",
        "included_fans": "fans_included",
        "max_fans": "max_fans",
        "has_front_usb_c": "has_front_usb_c",
    }
    for raw_key, raw_value in raw.items():
        key = clean_text(raw_key).lower().replace("specs.", "").replace("compatibility.", "").replace("scores.", "")
        key = re.sub(r"[^a-z0-9_]+", "_", key).strip("_")
        target = aliases.get(key)
        if not target:
            continue
        value = normalize_structured_value(target, raw_value)
        if value not in ("", None, [], {}):
            specs[target] = value


def normalize_structured_value(target: str, value: Any) -> Any:
    text = clean_text(value)
    if text == "":
        return None
    if target in {
        "cores",
        "threads",
        "tdp_w",
        "release_year",
        "memory_slots",
        "max_memory_gb",
        "max_memory_speed_mhz",
        "m2_slots",
        "sata_ports",
        "vram_gb",
        "bus_width_bit",
        "recommended_psu_w",
        "length_mm",
        "capacity_gb",
        "modules",
        "speed_mhz",
        "read_mb_s",
        "write_mb_s",
        "endurance_tbw",
        "wattage_w",
        "pcie_8pin_connectors",
        "warranty_years",
        "height_mm",
        "radiator_size_mm",
        "fan_count",
        "max_psu_length_mm",
        "fans_included",
        "max_fans",
    }:
        return to_int(text)
    if target in {"base_clock_ghz", "boost_clock_ghz", "noise_db"}:
        return to_float(text)
    if target in {"integrated_graphics", "wifi", "bluetooth", "cuda_support", "rgb", "has_dram_cache", "native_12vhpwr", "has_front_usb_c"}:
        return parse_bool(text)
    if target in {"socket_support", "motherboard_support", "supported_radiator_sizes"}:
        values = split_list_value(text)
        if target == "supported_radiator_sizes":
            return [to_int(item) for item in values if to_int(item) is not None]
        return values
    if target == "latency":
        number = to_int(text)
        return f"CL{number}" if number is not None else text.upper()
    return text


def parse_bool(text: str) -> Optional[bool]:
    lowered = text.lower()
    if lowered in {"true", "yes", "y", "1", "是", "有", "支持"}:
        return True
    if lowered in {"false", "no", "n", "0", "否", "无", "不支持"}:
        return False
    return None


def split_list_value(text: str) -> List[str]:
    text = text.strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            loaded = json.loads(text)
            if isinstance(loaded, list):
                return [clean_text(item) for item in loaded if clean_text(item)]
        except json.JSONDecodeError:
            pass
    return [clean_text(item) for item in re.split(r"[,，、/|]+", text) if clean_text(item)]


def extract_cpu_specs(specs: Dict[str, Any], text: str) -> None:
    socket = re.search(r"\b(?:LGA\s?\d{4}|AM[45])\b", text, re.I)
    specs["socket"] = socket.group(0).replace(" ", "").upper() if socket else ""
    specs["cores"] = parse_first_number(r"(\d+)\s*(?:核心|核)", text, int)
    specs["threads"] = parse_first_number(r"(\d+)\s*(?:线程|thread)", text, int)
    specs["base_clock_ghz"] = parse_first_number(r"(?:基础|base)[^\d]{0,8}(\d+(?:\.\d+)?)\s*GHz", text)
    specs["boost_clock_ghz"] = parse_first_number(r"(?:睿频|加速|boost|max)[^\d]{0,8}(\d+(?:\.\d+)?)\s*GHz", text)
    specs["tdp_w"] = parse_first_number(r"(\d{2,3})\s*W", text, int)
    specs["integrated_graphics"] = bool(re.search(r"核显|集显|UHD|Radeon\s+Graphics", text, re.I))
    generation = re.search(r"(第?\s*\d{1,2}\s*代|Zen\s*\d)", text, re.I)
    specs["generation"] = clean_text(generation.group(0)) if generation else ""


def extract_gpu_specs(specs: Dict[str, Any], text: str) -> None:
    chipset = re.search(r"\b(?:RTX|GTX|RX|ARC)\s*\d{3,5}\s*(?:TI|SUPER|XT|GRE)?\b", text, re.I)
    specs["chipset"] = clean_text(chipset.group(0).upper()) if chipset else ""
    vram = re.search(r"(\d+(?:\.\d+)?)\s*(?:G|GB)\s*(?:显存|GDDR)", text, re.I)
    specs["vram_gb"] = int(float(vram.group(1))) if vram else None
    memory_type = re.search(r"\bGDDR[56X]+\b", text, re.I)
    specs["memory_type"] = memory_type.group(0).upper() if memory_type else ""
    specs["bus_width_bit"] = parse_first_number(r"(\d{2,4})\s*(?:bit|位宽)", text, int)
    interface = re.search(r"PCIe?\s*[345]\.0|PCIe?\s*[345]", text, re.I)
    specs["interface"] = clean_text(interface.group(0).upper()) if interface else ""
    specs["length_mm"] = parse_first_number(r"(\d{2,3})\s*mm", text, int)
    specs["power_w"] = parse_first_number(r"(?:功耗|TDP|TBP)[^\d]{0,8}(\d{2,4})\s*W", text, int)
    specs["recommended_psu_w"] = parse_first_number(r"(?:建议|推荐)[^\d]{0,8}(\d{3,4})\s*W", text, int)


def extract_motherboard_specs(specs: Dict[str, Any], text: str) -> None:
    socket = re.search(r"\b(?:LGA\s?\d{4}|AM[45])\b", text, re.I)
    specs["socket"] = specs.get("socket") or (socket.group(0).replace(" ", "").upper() if socket else "")
    chipset = re.search(r"\b(?:B|H|Z|X|A)\d{3}[A-Z]?\b", text, re.I)
    specs["chipset"] = specs.get("chipset") or (chipset.group(0).upper() if chipset else "")
    form_factor = re.search(r"\b(?:E-ATX|ATX|M-ATX|MATX|Micro-ATX|ITX|Mini-ITX)\b", text, re.I)
    specs["form_factor"] = specs.get("form_factor") or (form_factor.group(0).upper().replace("MICRO-", "M-") if form_factor else "")
    memory_type = re.search(r"\bDDR[45]\b", text, re.I)
    specs["memory_type"] = specs.get("memory_type") or (memory_type.group(0).upper() if memory_type else "")
    specs["max_memory_gb"] = specs.get("max_memory_gb") or capacity_to_gb(re_search_text(r"最大(?:内存|容量)[^\d]*(\d+(?:\.\d+)?\s*(?:GB|G|TB|T))", text))
    specs["m2_slots"] = specs.get("m2_slots") or parse_first_number(r"(\d+)\s*(?:个|x)?\s*M\.?2", text, int)
    pcie = re.search(r"PCIe?\s*[345]\.0|PCIe?\s*[345]", text, re.I)
    specs["pcie_version"] = specs.get("pcie_version") or (clean_text(pcie.group(0).upper()) if pcie else "")


def extract_memory_specs(specs: Dict[str, Any], text: str) -> None:
    specs["capacity_gb"] = capacity_to_gb(text)
    modules = re.search(r"(\d+)\s*[xX*]\s*(\d+(?:\.\d+)?)\s*(G|GB)", text, re.I)
    if modules:
        specs["modules"] = int(modules.group(1))
        specs["capacity_gb"] = int(float(modules.group(1)) * float(modules.group(2)))
    memory_type = re.search(r"\bDDR[45]\b", text, re.I)
    specs["memory_type"] = memory_type.group(0).upper() if memory_type else ""
    specs["speed_mhz"] = parse_first_number(r"(\d{4,5})\s*(?:MHz|MT/s|频率)?", text, int)
    latency = re.search(r"\bCL\s?(\d{2,3})\b", text, re.I)
    specs["latency"] = f"CL{latency.group(1)}" if latency else ""
    specs["voltage_v"] = parse_first_number(r"(\d(?:\.\d+)?)\s*V", text)


def extract_ssd_specs(specs: Dict[str, Any], text: str) -> None:
    specs["capacity_gb"] = specs.get("capacity_gb") or capacity_to_gb(text)
    form_factor = re.search(r"\b(?:M\.?2\s*22(?:30|42|60|80|110)|M\.?2|2\.5英寸|2\.5\")\b", text, re.I)
    specs["form_factor"] = specs.get("form_factor") or (clean_text(form_factor.group(0).upper()) if form_factor else "")
    interface = re.search(r"\b(?:PCIe\s*[345]\.0|SATA(?:3\.0)?|USB\s*3\.\d)\b", text, re.I)
    specs["interface"] = specs.get("interface") or (clean_text(interface.group(0).upper()) if interface else "")
    specs["protocol"] = specs.get("protocol") or ("NVMe" if re.search(r"\bNVMe\b", text, re.I) else "")
    specs["read_mb_s"] = specs.get("read_mb_s") or parse_first_number(r"(?:读取|读速|read)[^\d]{0,8}(\d{3,5})\s*MB/s", text, int)
    specs["write_mb_s"] = specs.get("write_mb_s") or parse_first_number(r"(?:写入|写速|write)[^\d]{0,8}(\d{3,5})\s*MB/s", text, int)
    specs["endurance_tbw"] = specs.get("endurance_tbw") or parse_first_number(r"(\d{2,5})\s*TBW", text, int)


def extract_hdd_specs(specs: Dict[str, Any], text: str) -> None:
    specs["capacity_gb"] = capacity_to_gb(text)
    size = re.search(r"(2\.5|3\.5)\s*(?:英寸|\")", text, re.I)
    specs["size_inch"] = float(size.group(1)) if size else None
    specs["interface"] = "SATA" if re.search(r"\bSATA\b", text, re.I) else ""
    specs["rpm"] = parse_first_number(r"(\d{4,5})\s*(?:转|RPM)", text, int)
    specs["cache_mb"] = parse_first_number(r"(\d{2,4})\s*(?:M|MB)\s*(?:缓存)?", text, int)


def extract_psu_specs(specs: Dict[str, Any], text: str) -> None:
    specs["wattage_w"] = parse_first_number(r"(\d{3,4})\s*W", text, int)
    rating = re.search(r"(80\s*PLUS\s*)?(钛金|白金|金牌|铜牌|白牌|银牌|Titanium|Platinum|Gold|Bronze|White|Silver)", text, re.I)
    specs["efficiency_rating"] = specs.get("efficiency_rating") or (clean_text(rating.group(0)) if rating else "")
    if specs.get("modular"):
        pass
    elif "全模组" in text:
        specs["modular"] = "full"
    elif "半模组" in text:
        specs["modular"] = "semi"
    elif "非模组" in text:
        specs["modular"] = "none"
    atx = re.search(r"ATX\s*3\.\d", text, re.I)
    specs["atx_version"] = clean_text(atx.group(0).upper()) if atx else ""
    specs["pcie_8pin_connectors"] = specs.get("pcie_8pin_connectors") or parse_first_number(r"(\d+)\s*(?:个|x)?\s*(?:PCIe|显卡)?\s*(?:8pin|8\s*pin)", text, int)
    if "native_12vhpwr" not in specs:
        specs["native_12vhpwr"] = bool(re.search(r"12VHPWR|12V-?2x6", text, re.I))


def extract_case_specs(specs: Dict[str, Any], text: str) -> None:
    size = re.search(r"(全塔|中塔|小塔|M-ATX|MATX|Mini-ITX|ITX|ATX)", text, re.I)
    specs["case_size"] = specs.get("case_size") or (clean_text(size.group(0).upper()) if size else "")
    support = re.findall(r"\b(?:E-ATX|ATX|M-ATX|MATX|Micro-ATX|ITX|Mini-ITX)\b", text, re.I)
    specs["motherboard_support"] = specs.get("motherboard_support") or sorted({item.upper().replace("MICRO-", "M-") for item in support})
    specs["gpu_clearance_mm"] = specs.get("gpu_clearance_mm") or parse_first_number(r"(?:显卡|GPU)[^\d]{0,12}(\d{2,3})\s*mm", text, int)
    specs["cooler_clearance_mm"] = specs.get("cooler_clearance_mm") or parse_first_number(r"(?:散热器|风冷|限高)[^\d]{0,12}(\d{2,3})\s*mm", text, int)
    specs["radiator_support_mm"] = specs.get("radiator_support_mm") or parse_first_number(r"(\d{3})\s*(?:水冷|冷排)", text, int)
    if "fans_included" not in specs:
        specs["fans_included"] = parse_first_number(r"(?:标配|预装)[^\d]{0,8}(\d+)\s*(?:把|个)?\s*风扇", text, int) or parse_first_number(r"(?:标配|预装)风扇\s*(\d+)", text, int)
    specs["drive_bays"] = specs.get("drive_bays") or parse_first_number(r"(\d+)\s*(?:个|x)?\s*(?:硬盘位|HDD|SSD)", text, int)


def extract_cooler_specs(specs: Dict[str, Any], text: str) -> None:
    if specs.get("cooler_type"):
        pass
    elif ("水冷" in text and "无水冷" not in text) or re.search(r"\bAIO\b", text, re.I):
        specs["cooler_type"] = "liquid"
    elif "风冷" in text or "塔式" in text:
        specs["cooler_type"] = "air"
    socket_support = re.findall(r"\b(?:LGA\s?\d{4}|AM[45])\b", text, re.I)
    specs["socket_support"] = sorted({item.replace(" ", "").upper() for item in socket_support})
    specs["radiator_size_mm"] = specs.get("radiator_size_mm") or parse_first_number(r"(?:冷排|水冷)[^\d]{0,8}(\d{3})\s*mm", text, int) or parse_first_number(r"(\d{3})\s*(?:水冷|冷排)", text, int)
    specs["height_mm"] = specs.get("height_mm") or parse_first_number(r"(?:高度|限高)[^\d]{0,8}(\d{2,3})\s*mm", text, int)
    specs["fan_size_mm"] = parse_first_number(r"(\d{2,3})\s*mm\s*风扇", text, int)
    specs["tdp_w"] = specs.get("tdp_w") or parse_first_number(r"(\d{2,4})\s*W\s*(?:TDP|解热|散热)", text, int)
    specs["argb"] = bool(re.search(r"\bARGB\s*(?:true|是|有)?\b|幻彩|灯效", text, re.I)) and not bool(re.search(r"\bARGB\s*(?:false|否|无)", text, re.I))


def extract_case_fan_specs(specs: Dict[str, Any], text: str) -> None:
    specs["fan_size_mm"] = parse_first_number(r"(\d{2,3})\s*mm", text, int)
    specs["rpm"] = parse_first_number(r"(\d{3,5})\s*(?:RPM|转)", text, int)
    specs["airflow_cfm"] = parse_first_number(r"(\d+(?:\.\d+)?)\s*CFM", text)
    specs["noise_dba"] = parse_first_number(r"(\d+(?:\.\d+)?)\s*dBA", text)
    specs["pwm"] = bool(re.search(r"\bPWM\b", text, re.I))
    specs["argb"] = bool(re.search(r"\bARGB\b|幻彩|灯效", text, re.I))
    specs["pack_count"] = parse_first_number(r"(\d+)\s*(?:只|把|个|联包|套装)", text, int)


def extract_monitor_specs(specs: Dict[str, Any], text: str) -> None:
    specs["size_inch"] = parse_first_number(r"(\d{2}(?:\.\d+)?)\s*(?:英寸|寸|\")", text)
    resolution = re.search(r"\b(?:\d{3,4}\s*[xX*]\s*\d{3,4}|[248]K|QHD|UHD|FHD|WQHD)\b", text, re.I)
    specs["resolution"] = clean_text(resolution.group(0).upper().replace("*", "x")) if resolution else ""
    specs["refresh_rate_hz"] = parse_first_number(r"(\d{2,3})\s*Hz", text, int)
    panel = re.search(r"\b(?:IPS|OLED|VA|TN|Mini\s*LED|Fast\s*IPS)\b", text, re.I)
    specs["panel_type"] = clean_text(panel.group(0).upper()) if panel else ""
    specs["response_ms"] = parse_first_number(r"(\d+(?:\.\d+)?)\s*ms", text)
    gamut = re.search(r"(\d{2,3}%\s*(?:sRGB|DCI-P3|Adobe\s*RGB|NTSC))", text, re.I)
    specs["color_gamut"] = clean_text(gamut.group(1)) if gamut else ""
    specs["ports"] = sorted({port.upper() for port in re.findall(r"\b(?:HDMI|DP|DisplayPort|Type-C|USB-C)\b", text, re.I)})
    sync = re.search(r"\b(?:G-Sync|FreeSync|Adaptive Sync)\b", text, re.I)
    specs["sync"] = clean_text(sync.group(0)) if sync else ""


def extract_keyboard_specs(specs: Dict[str, Any], text: str) -> None:
    specs["connection"] = extract_connection(text)
    layout = re.search(r"(\d{2,3})\s*(?:键|key)", text, re.I)
    specs["layout"] = f"{layout.group(1)} keys" if layout else ""
    switch = re.search(r"(青轴|茶轴|红轴|银轴|黑轴|静音轴|磁轴|光轴|TTC|佳达隆|凯华)[^\s，,]{0,8}", text, re.I)
    specs["switch_type"] = clean_text(switch.group(0)) if switch else ""
    specs["backlight"] = bool(re.search(r"背光|RGB|ARGB|灯效", text, re.I))
    specs["hot_swappable"] = bool(re.search(r"热插拔|hot.?swap", text, re.I))
    keycap = re.search(r"\b(?:PBT|ABS)\b", text, re.I)
    specs["keycap_material"] = keycap.group(0).upper() if keycap else ""


def extract_mouse_specs(specs: Dict[str, Any], text: str) -> None:
    specs["connection"] = extract_connection(text)
    specs["dpi"] = parse_first_number(r"(\d{3,6})\s*DPI", text, int)
    specs["weight_g"] = parse_first_number(r"(\d{2,3}(?:\.\d+)?)\s*g", text)
    sensor = re.search(r"\b(?:PAW\d{4}|HERO\s?\d*K?|Focus\+?|3395|3950)\b", text, re.I)
    specs["sensor"] = clean_text(sensor.group(0)) if sensor else ""
    specs["buttons"] = parse_first_number(r"(\d+)\s*(?:键|button)", text, int)
    specs["battery_hours"] = parse_first_number(r"(\d{2,4})\s*(?:小时|h)", text, int)


def extract_headset_specs(specs: Dict[str, Any], text: str) -> None:
    specs["connection"] = extract_connection(text)
    specs["driver_mm"] = parse_first_number(r"(\d{2,3})\s*mm\s*(?:单元|驱动)", text, int)
    specs["impedance_ohm"] = parse_first_number(r"(\d{1,4})\s*(?:Ω|欧)", text, int)
    specs["microphone"] = bool(re.search(r"麦克风|麦|mic", text, re.I))
    specs["surround"] = bool(re.search(r"7\.1|环绕|surround", text, re.I))
    specs["weight_g"] = parse_first_number(r"(\d{2,4})\s*g", text)


def extract_speaker_specs(specs: Dict[str, Any], text: str) -> None:
    channel = re.search(r"\b([257]\.1|2\.0|2\.1)\b", text)
    specs["channels"] = channel.group(1) if channel else ""
    specs["power_w"] = parse_first_number(r"(\d{1,4})\s*W", text, int)
    specs["connection"] = extract_connection(text)
    frequency = re.search(r"(\d{2,5}\s*Hz\s*[-~]\s*\d{1,3}\s*k?Hz)", text, re.I)
    specs["frequency_response"] = clean_text(frequency.group(1)) if frequency else ""
    bt = re.search(r"蓝牙\s*(\d(?:\.\d)?)|Bluetooth\s*(\d(?:\.\d)?)", text, re.I)
    specs["bluetooth_version"] = next((group for group in bt.groups() if group), "") if bt else ""


def extract_network_card_specs(specs: Dict[str, Any], text: str) -> None:
    interface = re.search(r"\b(?:PCIe|PCI-E|USB|M\.2|RJ45)\b", text, re.I)
    specs["interface"] = interface.group(0).upper() if interface else ""
    wireless = re.search(r"\b(?:Wi-?Fi\s*[467]|802\.11[a-z/]+)\b", text, re.I)
    specs["wireless_standard"] = clean_text(wireless.group(0).upper()) if wireless else ""
    bt = re.search(r"蓝牙\s*(\d(?:\.\d)?)|Bluetooth\s*(\d(?:\.\d)?)", text, re.I)
    specs["bluetooth_version"] = next((group for group in bt.groups() if group), "") if bt else ""
    speed = re.search(r"(\d+(?:\.\d+)?)\s*(G|Gbps|M|Mbps)", text, re.I)
    if speed:
        number = float(speed.group(1))
        unit = speed.group(2).lower()
        specs["speed_mbps"] = int(number * 1000 if unit.startswith("g") else number)
    specs["antennas"] = parse_first_number(r"(\d+)\s*(?:根|天线)", text, int)


def extract_connection(text: str) -> str:
    values = []
    if re.search(r"2\.4G|无线", text, re.I):
        values.append("2.4g_wireless")
    if re.search(r"蓝牙|Bluetooth", text, re.I):
        values.append("bluetooth")
    if re.search(r"有线|USB|Type-C|USB-C", text, re.I):
        values.append("wired")
    if "三模" in text:
        values = ["wired", "2.4g_wireless", "bluetooth"]
    return ",".join(dict.fromkeys(values))


def order_specs_for_category(category: str, specs: Dict[str, Any]) -> Dict[str, Any]:
    spec = ACTIVE_PART_SPEC_BY_KEY.get(category)
    if not spec:
        return dict(sorted(specs.items()))
    ordered: Dict[str, Any] = {}
    for field_name in spec.fields:
        if field_name in specs:
            ordered[field_name] = specs[field_name]
    for key in sorted(specs):
        if key not in ordered:
            ordered[key] = specs[key]
    return ordered


def build_advice_pc_field_values(
    category: str,
    specs: Dict[str, Any],
    text: str,
    raw: Dict[str, Any],
) -> Dict[str, Any]:
    labels = ACTIVE_ADVICE_FIELD_LABELS.get(category, ())
    if not labels:
        return {}
    values: Dict[str, Any] = {}
    for label in labels:
        key = canonicalize_advice_field_label(label, category)
        values[label] = specs.get(key)
        if values[label] in ("", None, [], {}):
            values[label] = extract_value_for_advice_label(label, category, text, raw)
    return {key: value for key, value in values.items() if value not in ("", None, [], {})}


def product_to_part_card(product: Dict[str, Any]) -> Dict[str, Any]:
    source = product.get("source", {}) if isinstance(product.get("source"), dict) else {}
    raw = product.get("raw", {}) if isinstance(product.get("raw"), dict) else {}
    source_url = product.get("product_url") or raw.get("product_url") or raw.get("source_url") or source.get("url") or ""
    source_url = source_url if clean_text(source_url).startswith(("http://", "https://")) else ""
    part_type = COMPONENT_TO_DOC_PART_TYPE.get(product.get("component_type", ""), product.get("component_type", "unknown"))
    specs = product.get("standardized_specs", {}) if isinstance(product.get("standardized_specs"), dict) else {}
    brand = clean_text(product.get("brand", "")) or clean_text(raw.get("brand", ""))
    model = clean_text(product.get("model", "")) or clean_text(raw.get("model", ""))
    sku = clean_text(product.get("sku", "")) or clean_text(raw.get("sku", ""))
    part_id = build_part_id(part_type, brand, model, sku)
    card = {
        "part_id": part_id,
        "part_type": part_type,
        "brand": brand,
        "model": model,
        "series": infer_series(part_type, model, specs),
        "title": clean_text(product.get("title", "")),
        "price": product.get("price_cny"),
        "currency": product.get("currency") or "CNY",
        "is_available": raw.get("product_status", product.get("availability", "listed")) != "off_shelf",
        "specs": build_part_card_specs(part_type, specs, raw),
        "scores": build_default_scores(part_type, specs, raw),
        "compatibility": build_part_card_compatibility(part_type, specs, raw),
        "tags": build_tags(part_type, specs, raw),
        "selling_points": split_list_value(clean_text(raw.get("selling_points", ""))),
        "limitations": split_list_value(clean_text(raw.get("limitations", ""))),
        "recommendation_text": clean_text(raw.get("recommendation_text", "")),
        "source": {
            "platform": clean_text(raw.get("platform", "JD")) or "JD",
            "sku_id": sku or None,
            "source_url": source_url or None,
            "source_file": source.get("url") if clean_text(source.get("url", "")).endswith((".csv", ".json", ".txt")) else None,
            "image_url": product.get("image_url") or None,
            "screenshot_path": source.get("screenshot_path") or None,
            "raw_specs_text": clean_text(raw.get("raw_specs_text", "")) or clean_text(raw.get("source_note", "")) or None,
            "updated_at": utc_now_iso(),
        },
    }
    return prune_empty(card)


def build_part_id(part_type: str, brand: str, model: str, sku: str = "") -> str:
    if sku:
        return f"{part_type}_jd_{sku}"
    slug_source = "_".join(part for part in (brand, model) if part) or stable_id(part_type, brand, model)
    slug = re.sub(r"[^a-z0-9]+", "_", slug_source.lower()).strip("_")
    return f"{part_type}_{slug[:72]}"


def infer_series(part_type: str, model: str, specs: Dict[str, Any]) -> str:
    text = f"{model} {json.dumps(specs, ensure_ascii=False)}"
    if part_type == "cpu":
        match = re.search(r"(Ryzen\s+[3579]|Core\s+i[3579]|i[3579])", text, re.I)
        return clean_text(match.group(1)) if match else ""
    if part_type == "gpu":
        match = re.search(r"\b(RTX\s*\d{2}|RX\s*\d{3,4}|GTX\s*\d{2})", text, re.I)
        return clean_text(match.group(1).upper()) if match else ""
    return ""


def build_part_card_specs(part_type: str, specs: Dict[str, Any], raw: Dict[str, Any]) -> Dict[str, Any]:
    if part_type == "cpu":
        return prune_empty({
            "socket": specs.get("socket"),
            "core_count": specs.get("cores"),
            "thread_count": specs.get("threads"),
            "base_clock_ghz": specs.get("base_clock_ghz"),
            "boost_clock_ghz": specs.get("boost_clock_ghz"),
            "tdp_w": specs.get("tdp_w"),
            "has_integrated_gpu": specs.get("integrated_graphics"),
            "architecture": specs.get("architecture"),
            "platform": specs.get("platform") if specs.get("platform") not in {"JD", "jd"} else specs.get("socket"),
            "release_year": specs.get("release_year"),
        })
    if part_type == "motherboard":
        return prune_empty({
            "socket": specs.get("socket"),
            "chipset": specs.get("chipset"),
            "form_factor": specs.get("form_factor"),
            "memory_type": specs.get("memory_type"),
            "memory_slots": specs.get("memory_slots"),
            "max_memory_gb": specs.get("max_memory_gb"),
            "max_memory_speed_mhz": specs.get("max_memory_speed_mhz"),
            "pcie_x16_slots": specs.get("pcie_x16_slots"),
            "m2_slots": specs.get("m2_slots"),
            "sata_ports": specs.get("sata_ports"),
            "has_wifi": specs.get("wifi"),
            "has_bluetooth": specs.get("bluetooth"),
            "usb_c_front_header": specs.get("usb_c_front_header"),
            "usb_c_rear": specs.get("usb_c_rear"),
            "supported_cpu_series": split_list_value(clean_text(raw.get("supported_cpu_series", ""))),
            "bios_version_required": clean_text(raw.get("bios_version_required", "")) or None,
        })
    if part_type == "gpu":
        chipset = specs.get("chipset")
        chip_vendor = infer_gpu_vendor(clean_text(chipset or raw.get("chip_vendor", "")))
        return prune_empty({
            "chip_vendor": chip_vendor,
            "gpu_chip": chipset,
            "vram_gb": specs.get("vram_gb"),
            "vram_type": specs.get("memory_type"),
            "memory_bus_bit": specs.get("bus_width_bit"),
            "stream_processors": to_int(raw.get("stream_processors")),
            "tdp_w": specs.get("power_w") or specs.get("tdp_w"),
            "recommended_psu_w": specs.get("recommended_psu_w"),
            "power_connector": specs.get("power_connector"),
            "length_mm": specs.get("length_mm"),
            "slot_width": raw.get("slot_width"),
            "cooling_fans": raw.get("cooling_fans"),
            "rgb": specs.get("rgb") or raw.get("rgb"),
            "cuda_support": specs.get("cuda_support") if "cuda_support" in specs else chip_vendor == "NVIDIA",
            "tensor_core_generation": raw.get("tensor_core_generation"),
            "nvenc_generation": raw.get("nvenc_generation"),
            "release_year": specs.get("release_year"),
        })
    if part_type == "memory":
        return prune_empty({
            "memory_type": specs.get("memory_type"),
            "total_capacity_gb": specs.get("capacity_gb"),
            "stick_count": specs.get("modules"),
            "capacity_per_stick_gb": compute_capacity_per_stick(specs),
            "speed_mhz": specs.get("speed_mhz"),
            "latency_cl": to_int(specs.get("latency")),
            "has_rgb": specs.get("rgb"),
        })
    if part_type == "storage":
        return prune_empty({
            "storage_type": specs.get("storage_type") or ("HDD" if specs.get("rpm") else "SSD"),
            "capacity_gb": specs.get("capacity_gb"),
            "interface": specs.get("interface"),
            "protocol": specs.get("protocol"),
            "form_factor": specs.get("form_factor") or specs.get("size_inch"),
            "read_speed_mb_s": specs.get("read_mb_s"),
            "write_speed_mb_s": specs.get("write_mb_s"),
            "endurance_tbw": specs.get("endurance_tbw"),
            "has_dram_cache": specs.get("has_dram_cache"),
        })
    if part_type == "psu":
        return prune_empty({
            "wattage": specs.get("wattage_w"),
            "efficiency_rating": specs.get("efficiency_rating"),
            "modular_type": specs.get("modular"),
            "has_12vhpwr": specs.get("native_12vhpwr"),
            "has_12v_2x6": specs.get("native_12vhpwr"),
            "pcie_8pin_count": specs.get("pcie_8pin_connectors"),
            "cpu_8pin_count": to_int(raw.get("cpu_8pin_count")),
            "length_mm": specs.get("length_mm"),
            "warranty_years": specs.get("warranty_years"),
        })
    if part_type == "cooler":
        return prune_empty({
            "cooler_type": specs.get("cooler_type"),
            "supported_sockets": specs.get("socket_support"),
            "cooling_capacity_w": specs.get("tdp_w"),
            "height_mm": specs.get("height_mm"),
            "radiator_size_mm": specs.get("radiator_size_mm"),
            "fan_count": specs.get("fan_count"),
            "noise_db": specs.get("noise_db"),
            "has_rgb": specs.get("rgb") or specs.get("argb"),
        })
    if part_type == "case":
        return prune_empty({
            "case_type": specs.get("case_size"),
            "supported_motherboard_form_factors": specs.get("motherboard_support"),
            "max_gpu_length_mm": specs.get("gpu_clearance_mm"),
            "max_cpu_cooler_height_mm": specs.get("cooler_clearance_mm"),
            "max_psu_length_mm": specs.get("max_psu_length_mm"),
            "supported_radiator_sizes": specs.get("supported_radiator_sizes") or ([specs.get("radiator_support_mm")] if specs.get("radiator_support_mm") else []),
            "included_fans": specs.get("fans_included"),
            "max_fans": specs.get("max_fans"),
            "has_front_usb_c": specs.get("has_front_usb_c"),
            "has_rgb": specs.get("rgb"),
        })
    return prune_empty(dict(specs))


def build_part_card_compatibility(part_type: str, specs: Dict[str, Any], raw: Dict[str, Any]) -> Dict[str, Any]:
    if part_type == "cpu":
        return prune_empty({
            "requires_motherboard_socket": specs.get("socket"),
            "requires_memory_type": specs.get("memory_type"),
            "requires_cooler_socket": specs.get("socket"),
            "recommended_cooling_capacity_w": specs.get("tdp_w"),
        })
    if part_type == "motherboard":
        return prune_empty({
            "supports_cpu_socket": specs.get("socket"),
            "requires_memory_type": specs.get("memory_type"),
            "case_form_factor_required": specs.get("form_factor"),
            "supports_pcie_gpu": True,
        })
    if part_type == "gpu":
        return prune_empty({
            "requires_psu_w": specs.get("recommended_psu_w"),
            "requires_case_gpu_length_mm": specs.get("length_mm"),
            "requires_power_connector": specs.get("power_connector"),
            "cuda_required_workloads": False if infer_gpu_vendor(clean_text(specs.get("chipset", ""))) == "AMD" else None,
        })
    if part_type == "memory":
        return prune_empty({
            "requires_motherboard_memory_type": specs.get("memory_type"),
            "requires_memory_slots": specs.get("modules"),
            "recommended_for_development": (specs.get("capacity_gb") or 0) >= 32,
            "recommended_for_heavy_development": (specs.get("capacity_gb") or 0) >= 64,
        })
    if part_type == "storage":
        return prune_empty({
            "requires_m2_slot": "M.2" in clean_text(specs.get("form_factor", specs.get("interface", ""))).upper(),
            "requires_nvme_support": "NVME" in clean_text(specs.get("protocol", "")).upper(),
            "best_with_pcie_version": re_search_text(r"(PCIe\s*[345]\.0)", clean_text(specs.get("protocol", specs.get("interface", "")))),
        })
    if part_type == "psu":
        return prune_empty({
            "supports_required_total_wattage": specs.get("wattage_w"),
            "supports_pcie_8pin_gpu": bool(specs.get("pcie_8pin_connectors")),
            "supports_12v_2x6_gpu": specs.get("native_12vhpwr"),
            "requires_case_psu_length_mm": specs.get("length_mm"),
        })
    if part_type == "cooler":
        return prune_empty({
            "supports_cpu_sockets": specs.get("socket_support"),
            "requires_case_cpu_cooler_height_mm": specs.get("height_mm"),
            "recommended_cpu_tdp_w_max": specs.get("tdp_w"),
        })
    if part_type == "case":
        return prune_empty({
            "supports_motherboard_form_factors": specs.get("motherboard_support"),
            "supports_gpu_length_mm": specs.get("gpu_clearance_mm"),
            "supports_cpu_cooler_height_mm": specs.get("cooler_clearance_mm"),
            "supports_psu_length_mm": specs.get("max_psu_length_mm"),
            "supports_front_usb_c": specs.get("has_front_usb_c"),
        })
    return {}


def build_default_scores(part_type: str, specs: Dict[str, Any], raw: Dict[str, Any]) -> Dict[str, Any]:
    scores = {}
    for key, value in raw.items():
        normalized = clean_text(key).lower()
        if normalized.startswith("scores."):
            scores[normalized.split(".", 1)[1]] = to_float(value)
    if scores:
        return prune_empty(scores)
    defaults = {
        "cpu": {"gaming": None, "productivity": None, "compile": None, "multitask": None},
        "motherboard": {"vrm_quality": None, "expansion": None, "connectivity": None, "upgrade_potential": None},
        "gpu": {"gaming_1080p": None, "gaming_1440p": None, "gaming_4k": None, "ai": None, "rendering": None, "video_editing": None},
        "memory": {"gaming": None, "development": None, "multitask": None, "value": None},
        "storage": {"game_loading": None, "development": None, "large_file_transfer": None, "value": None},
        "psu": {"reliability": None, "noise": None, "value": None, "upgrade_margin": None},
        "cooler": {"cooling": None, "noise": None, "value": None, "installation": None},
        "case": {"airflow": None, "noise": None, "build_difficulty": None, "compatibility_space": None},
    }
    return defaults.get(part_type, {})


def build_tags(part_type: str, specs: Dict[str, Any], raw: Dict[str, Any]) -> List[str]:
    raw_tags = split_list_value(clean_text(raw.get("tags", "")))
    if raw_tags:
        return raw_tags
    tags = [part_type]
    for key in ("socket", "chipset", "memory_type", "form_factor", "capacity_gb", "wattage_w", "case_size"):
        value = specs.get(key)
        if value not in ("", None, [], {}):
            tags.append(str(value))
    return list(dict.fromkeys(tags))


def infer_gpu_vendor(chipset: str) -> str:
    text = chipset.upper()
    if "RTX" in text or "GTX" in text:
        return "NVIDIA"
    if "RX" in text or "RADEON" in text:
        return "AMD"
    if "ARC" in text:
        return "Intel"
    return ""


def compute_capacity_per_stick(specs: Dict[str, Any]) -> Optional[int]:
    total = specs.get("capacity_gb")
    modules = specs.get("modules")
    if isinstance(total, int) and isinstance(modules, int) and modules:
        return int(total / modules)
    return None


def prune_empty(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: prune_empty(child)
            for key, child in value.items()
            if child not in ("", [], {}) and child is not None
        }
    if isinstance(value, list):
        return [prune_empty(item) for item in value if item not in ("", [], {}) and item is not None]
    return value


def extract_value_for_advice_label(label: str, category: str, text: str, raw: Dict[str, Any]) -> Any:
    for raw_key, raw_value in raw.items():
        if clean_text(label).lower() in clean_text(raw_key).lower() or clean_text(raw_key).lower() in clean_text(label).lower():
            value = clean_text(raw_value)
            if value:
                return value

    canonical = canonicalize_advice_field_label(label, category)
    if canonical.endswith("_gb"):
        return capacity_to_gb(text)
    if canonical.endswith("_w"):
        return parse_first_number(r"(\d{2,4})\s*W", text, int)
    if canonical.endswith("_mm"):
        return parse_first_number(r"(\d{2,4})\s*mm", text, int)
    if canonical.endswith("_mhz"):
        return parse_first_number(r"(\d{3,5})\s*(?:MHz|MT/s)?", text, int)
    if canonical.endswith("_hz"):
        return parse_first_number(r"(\d{2,4})\s*Hz", text, int)
    if canonical == "brand":
        return extract_brand(text)
    if canonical == "model":
        return extract_model(text, brand=extract_brand(text), category=category)
    if canonical == "chipset":
        chipset = re.search(r"\b(?:RTX|GTX|RX|ARC)\s*\d{3,5}\s*(?:TI|SUPER|XT|GRE)?\b", text, re.I)
        return clean_text(chipset.group(0).upper()) if chipset else ""
    if canonical == "memory_type":
        memory = re.search(r"\b(?:GDDR[56X]+|DDR[45])\b", text, re.I)
        return memory.group(0).upper() if memory else ""
    if canonical == "bus_width_bit":
        return parse_first_number(r"(\d{2,4})\s*(?:bit|位宽)", text, int)
    if canonical == "interface":
        interface = re.search(r"\b(?:PCIe?\s*[345](?:\.0)?|SATA|M\.?2|USB(?:-C)?|Type-C|HDMI|DP)\b", text, re.I)
        return clean_text(interface.group(0).upper()) if interface else ""
    if canonical == "connection":
        return extract_connection(text)
    if canonical == "socket":
        socket = re.search(r"\b(?:LGA\s?\d{4}|AM[45])\b", text, re.I)
        return socket.group(0).replace(" ", "").upper() if socket else ""
    if canonical == "resolution":
        resolution = re.search(r"\b(?:\d{3,4}\s*[xX*]\s*\d{3,4}|[248]K|QHD|UHD|FHD|WQHD)\b", text, re.I)
        return clean_text(resolution.group(0).upper().replace("*", "x")) if resolution else ""
    return ""


def re_search_text(pattern: str, text: str, default: str = "") -> str:
    match = re.search(pattern, text, re.I)
    return match.group(1) if match else default


def deduplicate_products(products: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    unique: List[Dict[str, Any]] = []
    for product in products:
        key = product.get("sku") or product.get("product_url") or product.get("id")
        if key in seen:
            continue
        seen.add(key)
        unique.append(product)
    return unique


def write_products_json(products: Sequence[Dict[str, Any]], output_path: Path, field_spec_source: str = "builtin") -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": utc_now_iso(),
        "source_note": (
            "Collected from public pages/CSV/screenshots with slow, robots-aware settings. "
            "Records should be reviewed before production use."
        ),
        "field_spec_source": field_spec_source,
        "component_families": [
            {
                "key": spec.key,
                "name_zh": spec.zh_name,
                "fields": list(spec.fields),
                "advice_pc_field_labels": list(ACTIVE_ADVICE_FIELD_LABELS.get(spec.key, ())),
            }
            for spec in ACTIVE_PART_SPECS
        ],
        "products": list(products),
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_parts_json(products: Sequence[Dict[str, Any]], output_path: Path, field_spec_source: str = "builtin") -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    parts = [product_to_part_card(product) for product in products]
    output_path.write_text(json.dumps(parts, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest_path = output_path.with_name("parts_manifest.json")
    by_type: Dict[str, int] = {}
    for part in parts:
        by_type[part.get("part_type", "unknown")] = by_type.get(part.get("part_type", "unknown"), 0) + 1
    manifest = {
        "generated_at": utc_now_iso(),
        "field_spec_source": field_spec_source,
        "parts_path": str(output_path),
        "part_count": len(parts),
        "part_type_counts": dict(sorted(by_type.items())),
        "core_part_types": list(CORE_DOC_PART_TYPES),
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def write_classified_outputs(
    products: Sequence[Dict[str, Any]],
    output_dir: Path,
    field_spec_source: str = "builtin",
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for product in products:
        part_type = COMPONENT_TO_DOC_PART_TYPE.get(product.get("component_type", ""), product.get("component_type", "unknown"))
        grouped.setdefault(part_type, []).append(product)

    for part_type, group in grouped.items():
        group_dir = output_dir / part_type
        group_dir.mkdir(parents=True, exist_ok=True)
        (group_dir / "source").mkdir(exist_ok=True)
        (group_dir / "screenshots").mkdir(exist_ok=True)
        write_products_json(group, group_dir / "products.json", field_spec_source=field_spec_source)
        parts = [product_to_part_card(product) for product in group]
        (group_dir / "parts.json").write_text(json.dumps(parts, ensure_ascii=False, indent=2), encoding="utf-8")
        manifest = {
            "name": f"jd_{part_type}_products",
            "part_type": part_type,
            "created_at": utc_now_iso(),
            "products_path": str(group_dir / "products.json"),
            "parts_path": str(group_dir / "parts.json"),
            "product_count": len(group),
            "field_spec_source": field_spec_source,
            "pipeline": [
                "public JD item pages / CSV / product screenshots",
                "product information extraction",
                "field cleaning and standardization",
                "classified product JSON and advice_pc part cards",
            ],
        }
        (group_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Slow JD PC parts data extraction pipeline.")
    parser.add_argument("--url", action="append", default=[], help="Public JD shop/search/product URL. Can be used more than once.")
    parser.add_argument(
        "--component",
        action="append",
        choices=tuple(PART_SPEC_BY_KEY),
        default=[],
        help="Crawl a conservative JD public search page for one component key. Can be used more than once.",
    )
    parser.add_argument("--keyword", action="append", default=[], help="Custom JD public search keyword. Can be used more than once.")
    parser.add_argument("--all-components", action="store_true", help="Queue one conservative search URL for each of the 16 PC component families.")
    parser.add_argument("--csv", action="append", type=Path, default=[], help="CSV file with product rows. Can be used more than once.")
    parser.add_argument("--seed-csv", type=Path, default=DEFAULT_SEED_CSV, help="Local seed CSV used with --component/--all-components.")
    parser.add_argument("--no-seed-csv", action="store_true", help="Do not merge the local seed CSV for component runs.")
    parser.add_argument("--screenshot-dir", type=Path, help="Directory of product screenshots. Optional .json/.txt sidecars are read.")
    parser.add_argument("--enable-ocr", action="store_true", help="Try local pytesseract OCR for screenshots.")
    parser.add_argument("--capture-screenshots", action="store_true", help="Capture screenshots for crawled product pages if Playwright is installed.")
    parser.add_argument("--screenshot-output-dir", type=Path, default=DEFAULT_SCREENSHOT_OUTPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--parts-output", type=Path, help="Write unified docs/advice_pc style data/parts.json.")
    parser.add_argument("--classified-output-dir", type=Path, help="Write grouped products.json/parts.json files by part_type.")
    parser.add_argument("--field-spec-doc", type=Path, default=Path("docs/advice_pc.md"), help="Markdown doc that contains advice_pc section 16 field definitions.")
    parser.add_argument("--field-spec-section", default="十六", help="Section marker used to find the advice_pc field-definition section.")
    parser.add_argument("--no-advice-doc-fields", action="store_true", help="Use builtin component fields instead of parsing docs/advice_pc.md.")
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    parser.add_argument("--max-items", type=int, default=DEFAULT_MAX_ITEMS)
    parser.add_argument("--delay-seconds", type=float, default=DEFAULT_DELAY_SECONDS)
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--user-agent", default=os.environ.get("JD_PC_PARTS_USER_AGENT", DEFAULT_USER_AGENT))
    parser.add_argument("--no-strict-robots", action="store_true", help="Allow crawling when robots.txt cannot be loaded. Not recommended.")
    parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level), format="%(levelname)s: %(message)s")
    field_spec_source = configure_advice_pc_fields(
        doc_path=args.field_spec_doc,
        section_marker=args.field_spec_section,
        disabled=args.no_advice_doc_fields,
    )

    raw_products: List[RawProduct] = []
    crawl_urls = list(args.url)
    requested_components = set(args.component)
    for component in args.component:
        seed_urls = SAFE_COMPONENT_SEED_URLS.get(component, ())
        if seed_urls:
            crawl_urls.extend(seed_urls)
            LOGGER.info("Queued %s query-free seed item URLs for component %s", len(seed_urls), component)
        else:
            spec = PART_SPEC_BY_KEY[component]
            crawl_urls.append(build_jd_search_url(spec.jd_keywords[0]))
            LOGGER.warning(
                "Component %s has no query-free seed URLs; queued a search URL that strict robots checks may skip.",
                component,
            )
    for keyword in args.keyword:
        crawl_urls.append(build_jd_search_url(keyword))
    if args.all_components:
        requested_components.add("all")
        for seed_urls in SAFE_COMPONENT_SEED_URLS.values():
            crawl_urls.extend(seed_urls)
        LOGGER.info("--all-components will merge local seed CSV rows for all core parts and query-free URL seeds where available.")

    if crawl_urls:
        crawler = JdPublicCrawler(
            user_agent=args.user_agent,
            delay_seconds=args.delay_seconds,
            strict_robots=not args.no_strict_robots,
            timeout_seconds=args.timeout_seconds,
        )
        raw_products.extend(crawler.collect(crawl_urls, max_pages=args.max_pages, max_items=args.max_items))
        if args.capture_screenshots:
            crawler.capture_screenshots(raw_products, output_dir=args.screenshot_output_dir)

    if args.csv:
        raw_products.extend(load_csv_products(args.csv))

    if requested_components and not args.no_seed_csv:
        raw_products.extend(load_component_seed_products(args.seed_csv, sorted(requested_components)))

    if args.screenshot_dir:
        raw_products.extend(load_screenshot_products(args.screenshot_dir, enable_ocr=args.enable_ocr))

    if not raw_products:
        LOGGER.warning("No products collected. Provide --url, --csv, or --screenshot-dir.")

    normalized = deduplicate_products(normalize_product(product) for product in raw_products)
    write_products_json(normalized, args.output, field_spec_source=field_spec_source)
    if args.parts_output:
        write_parts_json(normalized, args.parts_output, field_spec_source=field_spec_source)
    if args.classified_output_dir:
        write_classified_outputs(normalized, args.classified_output_dir, field_spec_source=field_spec_source)
    LOGGER.info("Wrote %s products to %s", len(normalized), args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
