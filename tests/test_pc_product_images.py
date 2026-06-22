from pathlib import Path

from rag.recommendation.comparison import product_to_comparison_row
from rag.recommendation.package_builder import product_card_from_component
from rag.recommendation.product_loader import load_pc_parts_product_catalog
from rag.recommendation.session_state import CartItem, ShoppingSession, cart_snapshot
from rag.schemas import SelectedComponent


ROOT_DIR = Path(__file__).resolve().parents[1]


def test_all_pc_catalog_products_resolve_existing_static_images():
    catalog = load_pc_parts_product_catalog(use_cache=False)

    assert len(catalog.products) == 242
    for product in catalog.products:
        assert product.image_url.startswith("/pc-images/")
        assert product.image_path.startswith("data/jd_pc_products/")
        assert (ROOT_DIR / product.image_path).is_file()


def test_pc_card_and_comparison_preserve_image_url():
    product = load_pc_parts_product_catalog(use_cache=False).products[0]
    component = SelectedComponent(role=product.category, product=product)

    card = product_card_from_component(component, source="test")
    row = product_to_comparison_row(product)

    assert card["image_url"] == product.image_url
    assert row["image_url"] == product.image_url


def test_pc_cart_snapshot_preserves_image_url():
    catalog = load_pc_parts_product_catalog(use_cache=False)
    product = catalog.products[0]
    session = ShoppingSession(
        session_id="pc-image-cart-test",
        cart={product.product_id: CartItem(product_id=product.product_id)},
    )

    snapshot = cart_snapshot(session, catalog)

    assert snapshot["items"][0]["image_url"] == product.image_url
