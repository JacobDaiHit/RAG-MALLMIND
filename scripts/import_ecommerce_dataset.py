"""Import the provided ecommerce ZIP into the app's normalized catalog.

The source ZIP stores four category folders plus per-product JSON and images.
Folder names can be encoding-dependent, so this importer relies on stable
product ids and the JSON `image_path` values instead of trusting ZIP folder
names.
"""
from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path
from statistics import mean
from typing import Any, Dict, Iterable, List


ROOT_DIR = Path(__file__).resolve().parents[1]
SOURCE_ZIP = ROOT_DIR / "data" / "ecommerce_agent_dataset_供参考.zip"
TARGET_DIR = ROOT_DIR / "data" / "ecommerce_products"
IMAGE_DIR = TARGET_DIR / "images"
CATALOG_PATH = TARGET_DIR / "products.json"
MANIFEST_PATH = TARGET_DIR / "manifest.json"

CATEGORY_KEYS = {
    "美妆护肤": "beauty",
    "数码电子": "digital",
    "服饰运动": "clothing",
    "食品饮料": "food",
}


def main() -> None:
    if not SOURCE_ZIP.is_file():
        raise FileNotFoundError(f"Source ZIP not found: {SOURCE_ZIP}")

    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(SOURCE_ZIP) as archive:
        json_entries = sorted(name for name in archive.namelist() if name.endswith(".json"))
        image_entries = {
            Path(name).name: name
            for name in archive.namelist()
            if name.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
        }

        products = []
        for entry_name in json_entries:
            raw = json.loads(archive.read(entry_name).decode("utf-8"))
            product = normalize_product(raw)
            image_filename = Path(raw.get("image_path", "")).name or f"{product['product_id']}_live.jpg"
            zip_image_name = image_entries.get(image_filename)
            if zip_image_name:
                with archive.open(zip_image_name) as source, (IMAGE_DIR / image_filename).open("wb") as target:
                    shutil.copyfileobj(source, target)
            product["image_path"] = f"data/ecommerce_products/images/{image_filename}"
            product["image_url"] = f"/product-images/{image_filename}"
            products.append(product)

    products.sort(key=lambda item: (item["category_key"], item["product_id"]))
    CATALOG_PATH.write_text(json.dumps(products, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    MANIFEST_PATH.write_text(
        json.dumps(
            {
                "source_zip": str(SOURCE_ZIP.relative_to(ROOT_DIR)),
                "catalog": str(CATALOG_PATH.relative_to(ROOT_DIR)),
                "image_dir": str(IMAGE_DIR.relative_to(ROOT_DIR)),
                "product_count": len(products),
                "categories": category_counts(products),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Imported {len(products)} products into {CATALOG_PATH}")


def normalize_product(raw: Dict[str, Any]) -> Dict[str, Any]:
    product_id = str(raw["product_id"]).strip()
    title = compact(raw.get("title"))
    brand = compact(raw.get("brand"))
    category = compact(raw.get("category"))
    sub_category = compact(raw.get("sub_category"))
    skus = normalize_skus(raw.get("skus") or [])
    prices = [sku["price"] for sku in skus if sku.get("price") is not None]
    base_price = float(raw.get("base_price") or (min(prices) if prices else 0.0))
    knowledge = raw.get("rag_knowledge") or {}
    description = compact(knowledge.get("marketing_description"), limit=5000)
    faqs = normalize_faq(knowledge.get("official_faq") or [])
    reviews = normalize_reviews(knowledge.get("user_reviews") or [])
    ratings = [review["rating"] for review in reviews if review.get("rating") is not None]

    tags = build_tags(title, brand, category, sub_category, description, skus)
    best_for = infer_best_for(category, sub_category, description)
    not_good_for = infer_not_good_for(description, reviews)
    supported_scenarios = infer_supported_scenarios(category, sub_category, description)

    return {
        "product_id": product_id,
        "title": title,
        "brand": brand,
        "category": category,
        "category_key": CATEGORY_KEYS.get(category, "other"),
        "sub_category": sub_category,
        "base_price": base_price,
        "min_price": min(prices) if prices else base_price,
        "max_price": max(prices) if prices else base_price,
        "currency": "CNY",
        "stock_status": "available_for_demo",
        "stock_quantity": None,
        "skus": skus,
        "description": description,
        "faqs": faqs,
        "reviews": reviews,
        "review_count": len(reviews),
        "rating_avg": round(mean(ratings), 2) if ratings else None,
        "tags": tags,
        "best_for": best_for,
        "not_good_for": not_good_for,
        "supported_scenarios": supported_scenarios,
        "metadata": {
            "source": "ecommerce_agent_dataset_供参考.zip",
            "source_image_path": compact(raw.get("image_path")),
        },
    }


def normalize_skus(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    skus = []
    for item in items:
        try:
            price = float(item.get("price")) if item.get("price") is not None else None
        except (TypeError, ValueError):
            price = None
        skus.append(
            {
                "sku_id": compact(item.get("sku_id")),
                "properties": {
                    compact(key): compact(value)
                    for key, value in (item.get("properties") or {}).items()
                    if compact(key)
                },
                "price": price,
            }
        )
    return skus


def normalize_faq(items: Iterable[Dict[str, Any]]) -> List[Dict[str, str]]:
    return [
        {
            "question": compact(item.get("question"), 600),
            "answer": compact(item.get("answer"), 1600),
        }
        for item in items
        if compact(item.get("question")) or compact(item.get("answer"))
    ]


def normalize_reviews(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    reviews = []
    for item in items:
        rating = item.get("rating")
        try:
            rating_value = int(rating) if rating is not None else None
        except (TypeError, ValueError):
            rating_value = None
        reviews.append(
            {
                "nickname": compact(item.get("nickname"), 80),
                "rating": rating_value,
                "content": compact(item.get("content"), 1600),
            }
        )
    return reviews


def build_tags(
    title: str,
    brand: str,
    category: str,
    sub_category: str,
    description: str,
    skus: List[Dict[str, Any]],
) -> List[str]:
    tags = [brand, category, sub_category]
    for keyword in [
        "油皮",
        "干皮",
        "敏感肌",
        "保湿",
        "补水",
        "防晒",
        "控油",
        "淡纹",
        "轻量",
        "跑步",
        "通勤",
        "户外",
        "办公",
        "拍照",
        "续航",
        "游戏",
        "学习",
        "儿童",
        "低糖",
        "无糖",
        "咖啡",
        "旅行",
        "夏天",
        "冬天",
        "礼盒",
    ]:
        if keyword in title or keyword in description:
            tags.append(keyword)
    for sku in skus:
        for value in (sku.get("properties") or {}).values():
            tags.append(str(value))
    return dedupe(tags)[:24]


def infer_best_for(category: str, sub_category: str, description: str) -> List[str]:
    candidates = []
    if category == "美妆护肤":
        candidates.extend(["护肤咨询", "肤质匹配", "成分与功效问答"])
    elif category == "数码电子":
        candidates.extend(["参数对比", "预算筛选", "使用场景匹配"])
    elif category == "服饰运动":
        candidates.extend(["穿搭组合", "尺码颜色选择", "运动/通勤场景"])
    elif category == "食品饮料":
        candidates.extend(["口味偏好", "囤货采购", "场景化补给"])
    if sub_category:
        candidates.append(sub_category)
    if "旅行" in description or "出差" in description:
        candidates.append("旅行出差")
    if "学生" in description or "学习" in description:
        candidates.append("学生党")
    if "上班" in description or "办公" in description:
        candidates.append("上班通勤")
    return dedupe(candidates)[:8]


def infer_not_good_for(description: str, reviews: List[Dict[str, Any]]) -> List[str]:
    negatives = []
    text = description + " " + " ".join(review.get("content", "") for review in reviews if (review.get("rating") or 0) <= 2)
    for keyword, label in [
        ("油皮", "油皮需谨慎"),
        ("敏感", "极敏感人群需先试用"),
        ("咖啡因", "咖啡因敏感人群不建议"),
        ("甜", "严格控糖用户需看配料"),
        ("大屏", "偏爱小屏/轻便用户需谨慎"),
        ("厚", "追求极致轻薄用户需谨慎"),
    ]:
        if keyword in text:
            negatives.append(label)
    return dedupe(negatives)[:6]


def infer_supported_scenarios(category: str, sub_category: str, description: str) -> List[str]:
    scenarios = []
    if category == "美妆护肤":
        scenarios.extend(["beauty_routine", "skin_care", "gift_selection"])
    elif category == "数码电子":
        scenarios.extend(["digital_purchase", "device_comparison", "productivity"])
    elif category == "服饰运动":
        scenarios.extend(["outfit_bundle", "sportswear", "commute_style"])
    elif category == "食品饮料":
        scenarios.extend(["daily_grocery", "snack_drink", "stock_up"])
    if "运动" in description or "跑" in description:
        scenarios.append("sports")
    if "旅行" in description or "出差" in description:
        scenarios.append("travel")
    if "礼" in description:
        scenarios.append("gift")
    if sub_category:
        scenarios.append(sub_category)
    return dedupe(scenarios)[:10]


def category_counts(products: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for product in products:
        counts[product["category"]] = counts.get(product["category"], 0) + 1
    return counts


def dedupe(items: Iterable[Any]) -> List[str]:
    seen = set()
    result = []
    for item in items:
        value = compact(item)
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def compact(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())
    return text[:limit]


if __name__ == "__main__":
    main()
