import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
PRODUCTS_PATH = DATA / "ecommerce_products" / "products.json"
IMAGES_DIR = DATA / "ecommerce_products" / "images"


def _load_products():
    return json.loads(PRODUCTS_PATH.read_text(encoding="utf-8"))


def test_ecommerce_catalog_contains_all_dataset_products():
    products = _load_products()
    assert len(products) == 100
    assert {item["category"] for item in products} == {"美妆护肤", "数码电子", "服饰运动", "食品饮料"}


def test_each_product_has_card_and_rag_fields():
    missing = []
    for product in _load_products():
        for key in ("product_id", "title", "brand", "category", "sub_category", "base_price", "image_url", "description", "skus"):
            if not product.get(key):
                missing.append(f"{product.get('product_id', '<missing>')}: {key}")
        if not product.get("faqs"):
            missing.append(f"{product['product_id']}: faqs")
        if not product.get("reviews"):
            missing.append(f"{product['product_id']}: reviews")
    assert missing == []


def test_product_images_are_extracted_and_referenced():
    products = _load_products()
    image_files = {path.name for path in IMAGES_DIR.glob("*")}
    missing = []
    for product in products:
        filename = Path(product["image_url"]).name
        if filename not in image_files:
            missing.append(f"{product['product_id']}: {filename}")
    assert len(image_files) == 100
    assert missing == []


def test_old_api_catalog_data_is_not_present():
    assert not (DATA / "api_products").exists()
    assert not (DATA / "api_docs").exists()
    assert not (DATA / "price_rules").exists()
