"""Local image-vector retrieval for product images.

This module is the first real image-similarity layer. It intentionally uses a
small deterministic pixel embedding so the retrieval path is testable offline.
The embedding backend can later be replaced by CLIP/SigLIP without changing the
recommendation fusion contract.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import math
import os
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from PIL import Image, ImageStat

from rag.recommendation.product_loader import ProductCatalog, load_product_catalog
from rag.schemas import ApiProduct
from rag.utils.runtime_errors import public_error, sanitize_report


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_IMAGE_INDEX_PATH = ROOT_DIR / "data" / "image_vectors.json"
IMAGE_EMBEDDING_VERSION = "pixel-hist-v1"
IMAGE_EMBEDDING_DIM = 53
IMAGE_RETRIEVAL_ENABLED = os.getenv("RECOMMENDATION_ENABLE_IMAGE_RETRIEVAL", "true").lower() != "false"
IMAGE_RETRIEVAL_TOP_K = int(os.getenv("IMAGE_RETRIEVAL_TOP_K", "8"))
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ImageRetrievalEvidence:
    """Product image retrieval hits grouped by product id."""

    by_product_id: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    total_hits: int = 0
    status: str = "disabled"
    error: str = ""
    index_path: str = ""
    embedding_version: str = IMAGE_EMBEDDING_VERSION
    query_count: int = 0

    def to_trace(self) -> Dict[str, Any]:
        return sanitize_report({
            "status": self.status,
            "total_hits": self.total_hits,
            "matched_product_ids": sorted(self.by_product_id.keys()),
            "error": self.error,
            "index_path": self.index_path,
            "embedding_version": self.embedding_version,
            "query_count": self.query_count,
        })


class PixelImageEmbeddingService:
    """Small deterministic image embedding based on color and texture features."""

    version = IMAGE_EMBEDDING_VERSION
    dim = IMAGE_EMBEDDING_DIM

    def embed_bytes(self, raw: bytes) -> List[float]:
        with Image.open(BytesIO(raw)) as image:
            return self.embed_image(image)

    def embed_path(self, path: Path) -> List[float]:
        with Image.open(path) as image:
            return self.embed_image(image)

    def embed_image(self, image: Image.Image) -> List[float]:
        rgb = image.convert("RGB")
        width, height = rgb.size
        small = rgb.resize((32, 32))
        pixels = list(small.getdata())
        total = max(len(pixels), 1)

        channel_hist = [0.0] * 24
        brightness_hist = [0.0] * 16
        saturation_hist = [0.0] * 8
        warm_pixels = 0
        cool_pixels = 0
        dark_pixels = 0
        light_pixels = 0
        edge_sum = 0.0

        for y in range(32):
            for x in range(32):
                r, g, b = pixels[y * 32 + x]
                channel_hist[min(r // 32, 7)] += 1
                channel_hist[8 + min(g // 32, 7)] += 1
                channel_hist[16 + min(b // 32, 7)] += 1
                brightness = (r + g + b) / 3
                brightness_hist[min(int(brightness // 16), 15)] += 1
                saturation = max(r, g, b) - min(r, g, b)
                saturation_hist[min(int(saturation // 32), 7)] += 1
                warm_pixels += int(r > b + 18)
                cool_pixels += int(b > r + 18)
                dark_pixels += int(brightness < 80)
                light_pixels += int(brightness > 190)
                if x:
                    pr, pg, pb = pixels[y * 32 + x - 1]
                    edge_sum += abs(r - pr) + abs(g - pg) + abs(b - pb)
                if y:
                    pr, pg, pb = pixels[(y - 1) * 32 + x]
                    edge_sum += abs(r - pr) + abs(g - pg) + abs(b - pb)

        stat = ImageStat.Stat(small)
        mean_rgb = [value / 255 for value in stat.mean]
        std_rgb = [value / 128 for value in stat.stddev]
        aspect = min(width / max(height, 1), 4.0) / 4.0
        inverse_aspect = min(height / max(width, 1), 4.0) / 4.0
        features = [
            *(value / (total * 3) for value in channel_hist),
            *(value / total for value in brightness_hist),
            *(value / total for value in saturation_hist),
            *mean_rgb,
            *std_rgb,
            warm_pixels / total,
            cool_pixels / total,
            dark_pixels / total,
            light_pixels / total,
            min(edge_sum / (total * 255 * 6), 1.0),
            aspect,
            inverse_aspect,
        ]
        return l2_normalize(features)


class ProductImageVectorIndex:
    """Persistent local image vector index for catalog product images."""

    def __init__(
        self,
        *,
        index_path: Path | str = DEFAULT_IMAGE_INDEX_PATH,
        embedding_service: Optional[PixelImageEmbeddingService] = None,
    ) -> None:
        self.index_path = Path(index_path)
        self.embedding_service = embedding_service or PixelImageEmbeddingService()

    def load_or_build(self, catalog: Optional[ProductCatalog] = None) -> Dict[str, Any]:
        catalog = catalog or load_product_catalog()
        loaded = self._load()
        if self._is_usable(loaded, catalog):
            return loaded
        return self.build(catalog)

    def build(self, catalog: ProductCatalog) -> Dict[str, Any]:
        entries: List[Dict[str, Any]] = []
        for product in catalog.products:
            path = resolve_product_image_path(product)
            if path is None:
                continue
            try:
                vector = self.embedding_service.embed_path(path)
            except Exception as exc:
                logger.warning("Image embedding failed for catalog image: %s", exc)
                continue
            entries.append(
                {
                    "product_id": product.product_id,
                    "title": product.title,
                    "category": product.category.value,
                    "image_path": relative_path_text(path),
                    "image_sha256": sha256_file(path),
                    "vector": vector,
                }
            )
        payload = {
            "version": self.embedding_service.version,
            "dim": self.embedding_service.dim,
            "source": str(catalog.source_path),
            "count": len(entries),
            "entries": entries,
        }
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return payload

    def search(self, query_vectors: List[List[float]], catalog: ProductCatalog, top_k: int = IMAGE_RETRIEVAL_TOP_K) -> ImageRetrievalEvidence:
        if not IMAGE_RETRIEVAL_ENABLED:
            return ImageRetrievalEvidence(status="disabled", error="Image retrieval is disabled.")
        if not query_vectors:
            return ImageRetrievalEvidence(status="skipped", error="No image vectors were provided.")
        try:
            index = self.load_or_build(catalog)
            entries = index.get("entries") or []
            if not entries:
                return ImageRetrievalEvidence(status="empty", index_path=str(self.index_path), query_count=len(query_vectors))
            scored: Dict[str, Dict[str, Any]] = {}
            for query_index, query_vector in enumerate(query_vectors):
                for entry in entries:
                    score = cosine_similarity(query_vector, entry.get("vector") or [])
                    product_id = entry.get("product_id") or ""
                    if not product_id:
                        continue
                    previous = scored.get(product_id)
                    if previous is None or score > previous["score"]:
                        scored[product_id] = {
                            "product_id": product_id,
                            "title": entry.get("title", ""),
                            "category": entry.get("category", ""),
                            "score": round(score, 6),
                            "query_index": query_index,
                            "retrieval_mode": "image_vector",
                            "embedding_version": self.embedding_service.version,
                        }
            ranked = sorted(scored.values(), key=lambda item: item["score"], reverse=True)[:top_k]
            by_product_id = {item["product_id"]: [item] for item in ranked}
            return ImageRetrievalEvidence(
                by_product_id=by_product_id,
                total_hits=len(ranked),
                status="ok" if ranked else "empty",
                index_path=str(self.index_path),
                query_count=len(query_vectors),
            )
        except Exception as exc:
            logger.exception("Image retrieval failed")
            return ImageRetrievalEvidence(status="failed", error=public_error(exc), index_path=str(self.index_path), query_count=len(query_vectors))

    def _load(self) -> Dict[str, Any]:
        if not self.index_path.is_file():
            return {}
        try:
            return json.loads(self.index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Image vector index load failed: %s", exc)
            return {}

    def _is_usable(self, payload: Dict[str, Any], catalog: ProductCatalog) -> bool:
        if payload.get("version") != self.embedding_service.version:
            return False
        entries = payload.get("entries")
        if not isinstance(entries, list):
            return False
        expected_ids = {
            product.product_id
            for product in catalog.products
            if resolve_product_image_path(product) is not None
        }
        indexed_ids = {str(item.get("product_id")) for item in entries if isinstance(item, dict)}
        return bool(expected_ids) and expected_ids <= indexed_ids


def image_vectors_from_attachments(attachments: Iterable[Dict[str, Any]]) -> List[List[float]]:
    """Embed raw image data URLs from request attachments."""

    service = PixelImageEmbeddingService()
    vectors: List[List[float]] = []
    for item in attachments:
        if not isinstance(item, dict):
            continue
        data_url = item.get("data_url") or item.get("dataUrl")
        if not data_url:
            continue
        raw = decode_data_url(str(data_url))
        if not raw:
            continue
        try:
            vectors.append(service.embed_bytes(raw))
        except Exception as exc:
            logger.warning("Attachment image embedding failed: %s", exc)
            continue
    return vectors


def retrieve_image_evidence(
    *,
    attachments: Iterable[Dict[str, Any]],
    catalog: Optional[ProductCatalog] = None,
    top_k: int = IMAGE_RETRIEVAL_TOP_K,
    index: Optional[ProductImageVectorIndex] = None,
) -> ImageRetrievalEvidence:
    catalog = catalog or load_product_catalog()
    vectors = image_vectors_from_attachments(attachments)
    return (index or ProductImageVectorIndex()).search(vectors, catalog, top_k=top_k)


def resolve_product_image_path(product: ApiProduct) -> Optional[Path]:
    candidates: List[Path] = []
    if product.image_path:
        candidates.append(ROOT_DIR / str(product.image_path).replace("\\", "/"))
    if product.image_url:
        name = Path(str(product.image_url)).name
        if name:
            candidates.append(ROOT_DIR / "data" / "ecommerce_products" / "images" / name)
    for path in candidates:
        if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
            return path.resolve()
    return None


def decode_data_url(value: str) -> bytes:
    payload = value.split(",", 1)[1] if "," in value else value
    try:
        return base64.b64decode(payload, validate=True)
    except (ValueError, TypeError):
        return b""


def cosine_similarity(left: List[float], right: List[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right))


def l2_normalize(values: List[float]) -> List[float]:
    norm = math.sqrt(sum(value * value for value in values))
    if norm <= 0:
        return [0.0 for _ in values]
    return [float(value / norm) for value in values]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 64), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative_path_text(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT_DIR))
    except ValueError:
        return str(path)
