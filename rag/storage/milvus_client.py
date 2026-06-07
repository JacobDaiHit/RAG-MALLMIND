"""Milvus client for optional ecommerce product evidence retrieval."""
from __future__ import annotations

import os
import logging
import threading
from typing import Callable, TypeVar

from dotenv import load_dotenv  # noqa: E402 – retained for standalone scripts
from pymilvus import MilvusClient, DataType, AnnSearchRequest, RRFRanker

load_dotenv()
logger = logging.getLogger(__name__)

# Milvus 单次 query 的 limit 上限（超出会报 invalid max query result window）
QUERY_MAX_LIMIT = 16384
T = TypeVar("T")


class MilvusManager:
    """Milvus collection manager for dense+sparse product chunk retrieval."""

    def __init__(self):
        """初始化对象状态，保存后续方法会复用的配置、连接或依赖实例。"""
        self.host = os.getenv("MILVUS_HOST", "localhost")
        self.port = os.getenv("MILVUS_PORT", "19530")
        self.collection_name = os.getenv("MILVUS_COLLECTION", "embeddings_collection")
        self.uri = f"http://{self.host}:{self.port}"
        self.client = None
        self._client_lock = threading.RLock()

    def _get_client(self) -> MilvusClient:
        # Lazy-create client to avoid blocking app import/startup when Milvus is temporarily unavailable.
        """Milvus 客户端：获取 client，屏蔽配置、缓存或外部依赖细节。"""
        with self._client_lock:
            if self.client is None:
                self.client = MilvusClient(uri=self.uri)
            return self.client

    @staticmethod
    def _is_closed_channel_error(exc: Exception) -> bool:
        """Milvus 客户端：判断是否满足 closed channel error 条件。"""
        return isinstance(exc, ValueError) and "closed channel" in str(exc).lower()

    @staticmethod
    def _close_client(client) -> None:
        """Milvus 客户端：封装 close client 相关逻辑，供上层流程复用。"""
        close = getattr(client, "close", None)
        if not callable(close):
            return
        try:
            close()
        except Exception as exc:
            logger.warning("Milvus client close failed: %s", exc)
            pass

    def _reset_client(self, failed_client=None) -> None:
        """Milvus 客户端：重置 client 状态，避免上一次调用影响新流程。"""
        with self._client_lock:
            if self.client is None:
                return
            if failed_client is not None and self.client is not failed_client:
                return
            client = self.client
            self.client = None

        self._close_client(client)

    def _run_with_reconnect(self, operation: Callable[[MilvusClient], T]) -> T:
        """Milvus 客户端：运行 with reconnect 流程，并返回最终结果。"""
        client = self._get_client()
        try:
            return operation(client)
        except Exception as exc:
            if not self._is_closed_channel_error(exc):
                raise

            logger.warning("Milvus client channel closed; reconnecting: %s", exc)
            self._reset_client(client)
            return operation(self._get_client())

    def init_collection(self, dense_dim: int | None = None):
        """
        初始化 Milvus 集合 - 同时支持密集向量和稀疏向量
        :param dense_dim: 密集向量维度；默认读环境变量 DENSE_EMBEDDING_DIM（本地 BAAI/bge-m3 为 1024）
        """
        if dense_dim is None:
            from rag.ingestion.embedding import get_configured_embedding_dim
            dense_dim = get_configured_embedding_dim()
        def _init(client: MilvusClient) -> None:
            """Milvus 客户端：封装 init 相关逻辑，供上层流程复用。"""
            if client.has_collection(self.collection_name):
                self._validate_existing_dense_dim(client, dense_dim)
                return

            if not client.has_collection(self.collection_name):
                schema = client.create_schema(auto_id=True, enable_dynamic_field=True)
                
                # 主键
                schema.add_field("id", DataType.INT64, is_primary=True, auto_id=True)
                
                # 密集向量（来自 embedding 模型）
                schema.add_field("dense_embedding", DataType.FLOAT_VECTOR, dim=dense_dim)
                
                # 稀疏向量（来自 BM25）
                schema.add_field("sparse_embedding", DataType.SPARSE_FLOAT_VECTOR)
                
                # 文本和元数据字段
                schema.add_field("text", DataType.VARCHAR, max_length=8192)
                schema.add_field("filename", DataType.VARCHAR, max_length=255)
                schema.add_field("file_type", DataType.VARCHAR, max_length=50)
                schema.add_field("file_path", DataType.VARCHAR, max_length=1024)
                schema.add_field("page_number", DataType.INT64)
                schema.add_field("chunk_idx", DataType.INT64)

                # Chunk hierarchy fields are still useful for optional auto-merging.
                schema.add_field("chunk_id", DataType.VARCHAR, max_length=512)
                schema.add_field("parent_chunk_id", DataType.VARCHAR, max_length=512)
                schema.add_field("root_chunk_id", DataType.VARCHAR, max_length=512)
                schema.add_field("chunk_level", DataType.INT64)
                schema.add_field("product_id", DataType.VARCHAR, max_length=128)
                schema.add_field("title", DataType.VARCHAR, max_length=512)
                schema.add_field("brand", DataType.VARCHAR, max_length=128)
                schema.add_field("chunk_type", DataType.VARCHAR, max_length=64)
                schema.add_field("component_type", DataType.VARCHAR, max_length=64)
                schema.add_field("metadata", DataType.JSON)

                # 为两种向量分别创建索引
                index_params = client.prepare_index_params()
                
                # 密集向量索引 - 使用 HNSW（更适合混合检索）
                index_params.add_index(
                    field_name="dense_embedding",
                    index_type="HNSW",
                    metric_type="IP",
                    params={"M": 16, "efConstruction": 256}
                )
                
                # 稀疏向量索引
                index_params.add_index(
                    field_name="sparse_embedding",
                    index_type="SPARSE_INVERTED_INDEX",
                    metric_type="IP",
                    params={"drop_ratio_build": 0.2}
                )

                client.create_collection(
                    collection_name=self.collection_name,
                    schema=schema,
                    index_params=index_params
                )

        self._run_with_reconnect(_init)

    def _validate_existing_dense_dim(self, client: MilvusClient, expected_dim: int) -> None:
        existing_dim = self._describe_dense_embedding_dim(client)
        if existing_dim is None:
            return
        if existing_dim != expected_dim:
            raise ValueError(
                f"Milvus collection {self.collection_name!r} dense_embedding dim is {existing_dim}, "
                f"but current embedding dim is {expected_dim}. Rebuild the collection explicitly with --recreate/--rebuild."
            )

    def _describe_dense_embedding_dim(self, client: MilvusClient) -> int | None:
        describe = getattr(client, "describe_collection", None)
        if not callable(describe):
            return None
        info = describe(collection_name=self.collection_name)
        fields = []
        if isinstance(info, dict):
            schema = info.get("schema") or {}
            fields = info.get("fields") or schema.get("fields") or []
        else:
            schema = getattr(info, "schema", None)
            fields = getattr(info, "fields", None) or getattr(schema, "fields", None) or []
        for field in fields:
            if _field_value(field, "name") != "dense_embedding":
                continue
            return _field_dim(field)
        return None

    def insert(self, data: list[dict]):
        """插入数据到 Milvus"""
        return self._run_with_reconnect(lambda client: client.insert(self.collection_name, data))

    def flush(self) -> None:
        """Flush pending inserts so subsequent queries/searches can see new rows."""

        self._run_with_reconnect(lambda client: client.flush(self.collection_name))

    def query(
        self,
        filter_expr: str = "",
        output_fields: list[str] = None,
        limit: int = 10000,
        offset: int = 0,
    ):
        """查询数据。limit 不宜超过 QUERY_MAX_LIMIT。"""
        return self._run_with_reconnect(
            lambda client: client.query(
                collection_name=self.collection_name,
                filter=filter_expr,
                output_fields=output_fields or ["filename", "file_type"],
                limit=min(limit, QUERY_MAX_LIMIT),
                offset=offset,
            )
        )

    def query_all(self, filter_expr: str = "", output_fields: list[str] | None = None) -> list:
        """分页拉取匹配 filter 的全部行，避免单次 limit 超过服务端窗口。"""
        fields = output_fields or ["filename", "file_type"]
        out: list = []
        offset = 0
        while True:
            batch = self._run_with_reconnect(
                lambda client: client.query(
                    collection_name=self.collection_name,
                    filter=filter_expr,
                    output_fields=fields,
                    limit=QUERY_MAX_LIMIT,
                    offset=offset,
                )
            )
            if not batch:
                break
            out.extend(batch)
            if len(batch) < QUERY_MAX_LIMIT:
                break
            offset += len(batch)
        return out

    def get_chunks_by_ids(self, chunk_ids: list[str]) -> list[dict]:
        """根据 chunk_id 批量查询分块（用于 Auto-merging 拉取父块）"""
        ids = [item for item in chunk_ids if item]
        if not ids:
            return []
        quoted_ids = ", ".join([f'"{item}"' for item in ids])
        filter_expr = f"chunk_id in [{quoted_ids}]"
        return self.query(
            filter_expr=filter_expr,
            output_fields=[
                "text",
                "filename",
                "file_type",
                "page_number",
                "chunk_id",
                "parent_chunk_id",
                "root_chunk_id",
                "chunk_level",
                "chunk_idx",
            ],
            limit=len(ids),
        )

    def hybrid_retrieve(
        self,
        dense_embedding: list[float],
        sparse_embedding: dict,
        top_k: int = 5,
        rrf_k: int = 60,     #可调节
        filter_expr: str = "",
    ) -> list[dict]:
        """
        混合检索 - 使用 RRF 融合密集向量和稀疏向量的检索结果
        
        :param dense_embedding: 密集向量
        :param sparse_embedding: 稀疏向量 {index: value, ...}
        :param top_k: 返回结果数量
        :param rrf_k: RRF 算法参数 k，默认60
        :return: 检索结果列表
        """
        output_fields = [
            "text",
            "filename",
            "file_type",
            "page_number",
            "chunk_id",
            "parent_chunk_id",
            "root_chunk_id",
            "chunk_level",
            "chunk_idx",
            "product_id",
            "doc_type",
            "chunk_type",
            "category",
            "brand",
            "title",
        ]
        
        # 密集向量搜索请求
        dense_search = AnnSearchRequest(
            data=[dense_embedding],
            anns_field="dense_embedding",
            param={"metric_type": "IP", "params": {"ef": 64}},
            limit=top_k * 2,  # 多取一些用于融合
            expr=filter_expr,
        )
        
        # 稀疏向量搜索请求
        sparse_search = AnnSearchRequest(
            data=[sparse_embedding],
            anns_field="sparse_embedding",
            param={"metric_type": "IP", "params": {"drop_ratio_search": 0.2}},
            limit=top_k * 2,
            expr=filter_expr,
        )
        
        # 使用 RRF 排序算法融合结果
        reranker = RRFRanker(k=rrf_k)
        
        results = self._run_with_reconnect(
            lambda client: client.hybrid_search(
                collection_name=self.collection_name,
                reqs=[dense_search, sparse_search],
                ranker=reranker,
                limit=top_k,
                output_fields=output_fields
            )
        )
        
        # 格式化返回结果
        formatted_results = []
        for hits in results:
            for hit in hits:
                formatted_results.append({
                    "id": hit.get("id"),
                    "text": hit.get("text", ""),
                    "filename": hit.get("filename", ""),
                    "file_type": hit.get("file_type", ""),
                    "page_number": hit.get("page_number", 0),
                    "chunk_id": hit.get("chunk_id", ""),
                    "parent_chunk_id": hit.get("parent_chunk_id", ""),
                    "root_chunk_id": hit.get("root_chunk_id", ""),
                    "chunk_level": hit.get("chunk_level", 0),
                    "chunk_idx": hit.get("chunk_idx", 0),
                    "product_id": hit.get("product_id", ""),
                    "doc_type": hit.get("doc_type", ""),
                    "chunk_type": hit.get("chunk_type", ""),
                    "category": hit.get("category", ""),
                    "brand": hit.get("brand", ""),
                    "title": hit.get("title", ""),
                    "score": hit.get("distance", 0.0)
                })
        
        return formatted_results

    def dense_retrieve(self, dense_embedding: list[float], top_k: int = 5, filter_expr: str = "") -> list[dict]:
        """
        仅使用密集向量检索（降级模式，用于稀疏向量不可用时）
        """
        results = self._run_with_reconnect(
            lambda client: client.search(
                collection_name=self.collection_name,
                data=[dense_embedding],
                anns_field="dense_embedding",
                search_params={"metric_type": "IP", "params": {"ef": 64}},
                limit=top_k,
                output_fields=[
                    "text",
                    "filename",
                    "file_type",
                    "page_number",
                    "chunk_id",
                    "parent_chunk_id",
                    "root_chunk_id",
                    "chunk_level",
                    "chunk_idx",
                    "product_id",
                    "doc_type",
                    "chunk_type",
                    "category",
                    "brand",
                    "title",
                ],
                filter=filter_expr,
            )
        )
        
        formatted_results = []
        for hits in results:
            for hit in hits:
                formatted_results.append({
                    "id": hit.get("id"),
                    "text": hit.get("entity", {}).get("text", ""),
                    "filename": hit.get("entity", {}).get("filename", ""),
                    "file_type": hit.get("entity", {}).get("file_type", ""),
                    "page_number": hit.get("entity", {}).get("page_number", 0),
                    "chunk_id": hit.get("entity", {}).get("chunk_id", ""),
                    "parent_chunk_id": hit.get("entity", {}).get("parent_chunk_id", ""),
                    "root_chunk_id": hit.get("entity", {}).get("root_chunk_id", ""),
                    "chunk_level": hit.get("entity", {}).get("chunk_level", 0),
                    "chunk_idx": hit.get("entity", {}).get("chunk_idx", 0),
                    "product_id": hit.get("entity", {}).get("product_id", ""),
                    "doc_type": hit.get("entity", {}).get("doc_type", ""),
                    "chunk_type": hit.get("entity", {}).get("chunk_type", ""),
                    "category": hit.get("entity", {}).get("category", ""),
                    "brand": hit.get("entity", {}).get("brand", ""),
                    "title": hit.get("entity", {}).get("title", ""),
                    "score": hit.get("distance", 0.0)
                })
        
        return formatted_results

    def delete(self, filter_expr: str):
        """删除数据"""
        return self._run_with_reconnect(
            lambda client: client.delete(
                collection_name=self.collection_name,
                filter=filter_expr
            )
        )

    def has_collection(self) -> bool:
        """检查集合是否存在"""
        return self._run_with_reconnect(lambda client: client.has_collection(self.collection_name))

    def drop_collection(self):
        """删除集合（用于重建 schema）"""
        def _drop(client: MilvusClient) -> None:
            """Milvus 客户端：封装 drop 相关逻辑，供上层流程复用。"""
            if client.has_collection(self.collection_name):
                client.drop_collection(self.collection_name)

        self._run_with_reconnect(_drop)


def _parse_positive_int(value: object, default: int) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _field_value(field: object, key: str):
    if isinstance(field, dict):
        return field.get(key)
    return getattr(field, key, None)


def _field_dim(field: object) -> int | None:
    params = _field_value(field, "params") or {}
    if isinstance(params, dict):
        for key in ("dim", "dimension"):
            if key in params:
                return _parse_positive_int(params.get(key), default=0) or None
    dim = _field_value(field, "dim")
    if dim is not None:
        return _parse_positive_int(dim, default=0) or None
    return None
