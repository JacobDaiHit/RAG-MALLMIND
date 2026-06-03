import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "data" / "ecommerce_products" / "products.json"
MANIFEST_PATH = ROOT / "data" / "ecommerce_products" / "manifest.json"


def test_import_manifest_points_to_normalized_catalog():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    assert manifest["product_count"] == 100
    assert manifest["catalog"].endswith("data\\ecommerce_products\\products.json") or manifest["catalog"].endswith("data/ecommerce_products/products.json")


def test_normalized_catalog_preserves_sku_price_ranges():
    products = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    sample = next(item for item in products if item["product_id"] == "p_beauty_001")

    prices = [sku["price"] for sku in sample["skus"]]
    assert sample["min_price"] == min(prices)
    assert sample["max_price"] == max(prices)
    assert sample["currency"] == "CNY"


def test_android_card_fields_are_available():
    products = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    sample = products[0]

    assert sample["product_id"]
    assert sample["title"]
    assert sample["image_url"].startswith("/product-images/")
    assert Path(sample["image_path"]).name == Path(sample["image_url"]).name
