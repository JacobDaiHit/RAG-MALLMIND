import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from rag.api.app_context import (
    ALLOWED_ORIGINS,
    CORS_ALLOW_CREDENTIALS,
    FRONTEND_DIR,
    PC_IMAGES_DIR,
    PRODUCT_IMAGES_DIR,
    STREAM_LLM_ENABLED,
    VALIDATION_VERSION,
    build_complete_prompt,
    build_requirement_questions,
    prepare_recommendation_context,
)
from rag.api.attachments import (
    MAX_ATTACHMENT_ANALYSIS_BYTES,
    VISION_MODEL_NAME,
)
from rag.api.pc_build import router as pc_build_router
from rag.api.products import router as product_router
from rag.api.routes.attachments import router as attachment_router
from rag.api.routes.chat import router as chat_router
from rag.api.routes.feedback import router as feedback_router
from rag.api.routes.recommend import router as recommendation_router
from rag.api.sse import sse_event
from rag.recommendation import recommend_shopping_products
from rag.recommendation.image_retrieval import retrieve_image_evidence
from rag.recommendation.llm_client import OpenAICompatibleChatClient, get_llm_provider_trace, is_llm_configured, report_to_dict
from rag.recommendation.product_loader import load_catalog_for_scope, load_combined_product_catalog
from rag.recommendation.session_state import get_session
from rag.utils.runtime_errors import is_debug_mode, sanitize_report, sanitize_text


def create_app() -> FastAPI:
    app = FastAPI(
        title="MallMind Ecommerce Shopping Guide RAG",
        description="Grounded ecommerce shopping guide with streaming recommendations, PC build plans, and cart closure.",
        version="0.2.0",
    )
    if FRONTEND_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
    if PRODUCT_IMAGES_DIR.is_dir():
        app.mount("/product-images", StaticFiles(directory=PRODUCT_IMAGES_DIR), name="product-images")
    if PC_IMAGES_DIR.is_dir():
        app.mount("/pc-images", StaticFiles(directory=PC_IMAGES_DIR), name="pc-images")

    app.include_router(product_router)
    app.include_router(pc_build_router)
    app.include_router(attachment_router)
    app.include_router(feedback_router)
    app.include_router(recommendation_router)
    app.include_router(chat_router)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_credentials=CORS_ALLOW_CREDENTIALS,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── 启动预热：避免首次请求触发全部懒加载 ──
    @app.on_event("startup")
    def _warm_up():
        import logging
        import time
        _log = logging.getLogger("startup")
        started = time.monotonic()

        # 1) 产品目录（触发 lru_cache 磁盘读取 + JSON 解析）
        from rag.recommendation.product_loader import load_combined_product_catalog
        load_combined_product_catalog(use_cache=True)
        _log.info("product catalog warmed in %.1fs", time.monotonic() - started)

        milvus_enabled = os.getenv("RECOMMENDATION_ENABLE_MILVUS", "false").strip().lower() == "true"

        # 2) 嵌入服务（Milvus 检索开启时才触发模型/API 连接）
        if milvus_enabled:
            try:
                from rag.ingestion.embedding import embedding_service
                _ = embedding_service.provider  # 触发懒代理
                _log.info("embedding service warmed in %.1fs", time.monotonic() - started)
            except Exception as exc:
                _log.warning("embedding warm-up skipped: %s", exc)
        else:
            _log.info("embedding warm-up skipped: milvus disabled")

        # 3) Milvus 连接（仅在显式开启向量检索时触发 TCP 建连）
        if milvus_enabled:
            try:
                from rag.storage.milvus_client import MilvusManager
                mgr = MilvusManager()
                mgr.has_collection()
                _log.info("milvus warmed in %.1fs", time.monotonic() - started)
            except Exception as exc:
                _log.warning("milvus warm-up skipped: %s", exc)
        else:
            _log.info("milvus warm-up skipped: disabled")

        # 4) 会话存储（触发 Redis 连接或内存存储初始化）
        try:
            from rag.recommendation.session_state import get_session_store
            get_session_store()
            _log.info("session store warmed in %.1fs", time.monotonic() - started)
        except Exception as exc:
            _log.warning("session store warm-up skipped: %s", exc)

        _log.info("startup warm-up complete in %.1fs", time.monotonic() - started)

    return app


app = create_app()


@app.get("/health")
@app.get("/api/health")
def health() -> Dict[str, Any]:
    catalog_loaded, product_count, pc_product_count = _catalog_health_counts()
    return {
        "status": "ok",
        "app_env": os.getenv("APP_ENV", "development"),
        "catalog_loaded": catalog_loaded,
        "product_count": product_count,
        "pc_product_count": pc_product_count,
        "generation_model_configured": is_llm_configured(),
        "llm_configured": is_llm_configured(),
        **get_llm_provider_trace(),
        "redis_configured": bool(os.getenv("REDIS_URL")),
        "milvus_enabled": os.getenv("RECOMMENDATION_ENABLE_MILVUS", "false").strip().lower() == "true",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/runtime/diagnostics")
def runtime_diagnostics(x_admin_token: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    if not _diagnostics_allowed(x_admin_token):
        raise HTTPException(status_code=403, detail="diagnostics require debug mode or admin token")
    catalog_loaded, product_count, pc_product_count = _catalog_health_counts()
    payload = {
        "status": "ok",
        "app_env": os.getenv("APP_ENV", "development"),
        "runtime": {
            "stream_llm_enabled": STREAM_LLM_ENABLED,
            "recommendation_llm_parse": os.getenv("RECOMMENDATION_LLM_PARSE", "auto"),
            "recommendation_llm_guidance": os.getenv("RECOMMENDATION_LLM_GUIDANCE", "false"),
            "recommendation_enable_milvus": os.getenv("RECOMMENDATION_ENABLE_MILVUS", "false"),
        },
        "catalog": {
            "catalog_loaded": catalog_loaded,
            "product_count": product_count,
            "pc_product_count": pc_product_count,
        },
        "llm": _llm_diagnostics(),
        "redis": _redis_diagnostics(),
        "milvus": _milvus_diagnostics(),
        "db": _db_diagnostics(),
        "bm25": _bm25_diagnostics(),
        "indexes": {
            "image_vectors": (Path(__file__).resolve().parents[2] / "data" / "image_vectors.json").is_file(),
            "product_images_mounted": PRODUCT_IMAGES_DIR.is_dir(),
            "pc_product_assets_mounted": PC_IMAGES_DIR.is_dir(),
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if is_debug_mode():
        payload["debug"] = {
            "validation_version": VALIDATION_VERSION,
            "app_file": str(Path(__file__).resolve()),
            "attachment_analysis_max_bytes": MAX_ATTACHMENT_ANALYSIS_BYTES,
            "vision_model": VISION_MODEL_NAME or "MODEL",
        }
    return sanitize_report(payload)


@app.get("/api/llm/diagnose")
def diagnose_llm(model: Optional[str] = Query(default=None)) -> Dict[str, Any]:
    client = OpenAICompatibleChatClient()
    return report_to_dict(client.diagnose(model=model))


def _catalog_health_counts() -> Tuple[bool, int, int]:
    try:
        catalog = load_combined_product_catalog()
        pc_catalog = load_catalog_for_scope("pc_parts")
        return True, len(catalog.products), len(pc_catalog.products)
    except Exception:
        return False, 0, 0


def _diagnostics_allowed(x_admin_token: Optional[str]) -> bool:
    if is_debug_mode():
        return True
    expected = os.getenv("ADMIN_TOKEN", "").strip()
    return bool(expected and x_admin_token == expected)


def _llm_diagnostics() -> Dict[str, Any]:
    configured = is_llm_configured()
    trace = get_llm_provider_trace()
    if not configured:
        return {"configured": False, **trace}
    try:
        return {"configured": True, **trace, "diagnose": report_to_dict(OpenAICompatibleChatClient().diagnose())}
    except Exception as exc:
        return {"configured": True, **trace, "status": "failed", "error": sanitize_text(exc)}


def _redis_diagnostics() -> Dict[str, Any]:
    url = os.getenv("REDIS_URL", "")
    if not url:
        return {"configured": False}
    try:
        import redis

        client = redis.Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1)
        return {"configured": True, "ping": bool(client.ping())}
    except Exception as exc:
        return {"configured": True, "ping": False, "error": sanitize_text(exc)}


def _milvus_diagnostics() -> Dict[str, Any]:
    enabled = os.getenv("RECOMMENDATION_ENABLE_MILVUS", "false").strip().lower() == "true"
    if not enabled:
        return {"enabled": False}
    try:
        from rag.storage.milvus_client import MilvusManager

        manager = MilvusManager()
        return {"enabled": True, "has_collection": manager.has_collection(), "collection": manager.collection_name if is_debug_mode() else "configured"}
    except Exception as exc:
        return {"enabled": True, "status": "failed", "error": sanitize_text(exc)}


def _db_diagnostics() -> Dict[str, Any]:
    if not os.getenv("DATABASE_URL"):
        return {"configured": False}
    try:
        from sqlalchemy import text
        from rag.storage.database import engine

        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"configured": True, "reachable": True}
    except Exception as exc:
        return {"configured": True, "reachable": False, "error": sanitize_text(exc)}


def _bm25_diagnostics() -> Dict[str, Any]:
    path = Path(os.getenv("BM25_STATE_PATH", Path(__file__).resolve().parents[2] / "data" / "bm25_state.json"))
    return {"state_exists": path.is_file(), "state_path": str(path) if is_debug_mode() else "configured"}


@app.get("/")
def index() -> FileResponse:
    index_path = FRONTEND_DIR / "index.html"
    if not index_path.is_file():
        raise HTTPException(status_code=404, detail="frontend/index.html not found")
    return FileResponse(index_path)
