"""文本向量化服务 - 支持密集向量和稀疏向量（BM25），词表与 df 持久化 + 增量更新"""
from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from langchain_huggingface import HuggingFaceEmbeddings

load_dotenv()

_DEFAULT_STATE_PATH = Path(__file__).resolve().parents[2] / "data" / "bm25_state.json"
_EMPTY_SPARSE_TOKEN = "__empty_sparse__"
_EMPTY_SPARSE_WEIGHT = 1e-9
DEFAULT_DENSE_EMBEDDING_DIM = 1024
DEFAULT_DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


class EmbeddingProviderError(RuntimeError):
    """Raised when a configured dense embedding provider cannot return vectors."""


def _parse_positive_int(value: object, default: int) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


BM25_STATE_REPLACE_RETRIES = _parse_positive_int(os.getenv("BM25_STATE_REPLACE_RETRIES", "5"), 5)
BM25_STATE_REPLACE_RETRY_SECONDS = float(os.getenv("BM25_STATE_REPLACE_RETRY_SECONDS", "0.2"))


def get_configured_embedding_dim() -> int:
    """Return the configured dense embedding dimension.

    EMBEDDING_DIM takes precedence so provider smoke checks and indexing scripts
    can override the older DENSE_EMBEDDING_DIM name without changing Milvus envs.
    """

    raw = os.getenv("EMBEDDING_DIM") or os.getenv("DENSE_EMBEDDING_DIM", str(DEFAULT_DENSE_EMBEDDING_DIM))
    return _parse_positive_int(raw, DEFAULT_DENSE_EMBEDDING_DIM)


def get_configured_embedding_batch_size() -> int:
    return min(10, _parse_positive_int(os.getenv("EMBEDDING_BATCH_SIZE", "10"), 10))


class BaseEmbeddingProvider:
    provider = "base"
    model = ""
    dim = DEFAULT_DENSE_EMBEDDING_DIM

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    def embed_query(self, text: str) -> list[float]:
        vectors = self.embed_texts([text])
        if not vectors:
            raise EmbeddingProviderError(f"Embedding provider {self.provider}/{self.model} returned no query vector.")
        return vectors[0]


class LocalEmbeddingProvider(BaseEmbeddingProvider):
    provider = "local"

    def __init__(self, model: str | None = None, dim: int | None = None):
        self.model = model or os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
        self.dim = dim or get_configured_embedding_dim()
        self._embedder: HuggingFaceEmbeddings | None = None

    def _get_embedder(self) -> HuggingFaceEmbeddings:
        if self._embedder is None:
            self._embedder = _create_dense_embedder()
        return self._embedder

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            return self._get_embedder().embed_documents(texts)
        except Exception as exc:
            raise EmbeddingProviderError(f"Embedding provider local/{self.model} failed: {exc}") from exc


class OpenAICompatibleEmbeddingProvider(BaseEmbeddingProvider):
    """OpenAI-compatible embedding provider used by DashScope compatible mode."""

    def __init__(
        self,
        *,
        provider: str,
        model: str | None = None,
        dim: int | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        batch_size: int | None = None,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
        client: Any | None = None,
    ):
        self.provider = provider
        self.model = _provider_default_model(provider, model)
        self.dim = dim or get_configured_embedding_dim()
        self.base_url = base_url or (
            os.getenv("EMBEDDING_BASE_URL") or DEFAULT_DASHSCOPE_BASE_URL
            if provider == "dashscope"
            else os.getenv("EMBEDDING_BASE_URL") or os.getenv("BASE_URL", "")
        )
        self.api_key = api_key or _embedding_api_key(provider)
        self.batch_size = min(10, batch_size or get_configured_embedding_batch_size())
        self.timeout_seconds = float(timeout_seconds or os.getenv("EMBEDDING_TIMEOUT_SECONDS", "30"))
        self.max_retries = _parse_positive_int(os.getenv("EMBEDDING_MAX_RETRIES", str(max_retries or 2)), max_retries or 2)
        self._client = client

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self.api_key:
            raise EmbeddingProviderError(
                f"Embedding provider {self.provider}/{self.model} requires EMBEDDING_API_KEY"
                + (" or DASHSCOPE_API_KEY." if self.provider == "dashscope" else " or OPENAI_API_KEY.")
            )
        try:
            from openai import OpenAI
        except Exception:
            self._client = _RequestsCompatibleEmbeddingClient(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout_seconds=self.timeout_seconds,
                max_retries=self.max_retries,
            )
            return self._client

        kwargs: dict[str, Any] = {
            "api_key": self.api_key,
            "timeout": self.timeout_seconds,
            "max_retries": self.max_retries,
        }
        if self.base_url:
            kwargs["base_url"] = self.base_url
        self._client = OpenAI(**kwargs)
        return self._client

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        cleaned = [_clean_embedding_text(text) for text in texts]
        if not cleaned:
            return []

        vectors: list[list[float]] = []
        for start in range(0, len(cleaned), self.batch_size):
            batch = cleaned[start : start + self.batch_size]
            try:
                response = self._get_client().embeddings.create(
                    model=self.model,
                    input=batch,
                    dimensions=self.dim,
                )
            except Exception as exc:
                raise EmbeddingProviderError(
                    f"Embedding provider {self.provider}/{self.model} request failed: {self._sanitize_error(exc)}"
                ) from exc

            batch_vectors = [_embedding_vector(item) for item in _embedding_response_data(response)]
            for vector in batch_vectors:
                if len(vector) != self.dim:
                    raise EmbeddingProviderError(
                        f"Embedding provider {self.provider}/{self.model} returned dimension {len(vector)}, expected {self.dim}."
                    )
            if len(batch_vectors) != len(batch):
                raise EmbeddingProviderError(
                    f"Embedding provider {self.provider}/{self.model} returned {len(batch_vectors)} vectors for {len(batch)} inputs."
                )
            vectors.extend(batch_vectors)
        return vectors

    def _sanitize_error(self, exc: Exception) -> str:
        text = str(exc)
        if self.api_key:
            text = text.replace(self.api_key, "[REDACTED]")
        return text


class _RequestsCompatibleEmbeddingClient:
    def __init__(self, *, api_key: str, base_url: str, timeout_seconds: float, max_retries: int):
        self.embeddings = _RequestsCompatibleEmbeddings(
            api_key=api_key,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        )


class _RequestsCompatibleEmbeddings:
    def __init__(self, *, api_key: str, base_url: str, timeout_seconds: float, max_retries: int):
        self.api_key = api_key
        self.base_url = (base_url or DEFAULT_DASHSCOPE_BASE_URL).rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(0, int(max_retries))

    def create(self, *, model: str, input: list[str], dimensions: int) -> dict[str, Any]:
        url = f"{self.base_url}/embeddings"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "input": input,
            "dimensions": dimensions,
        }
        last_exc: Exception | None = None
        for _ in range(self.max_retries + 1):
            try:
                response = requests.post(url, headers=headers, json=payload, timeout=self.timeout_seconds)
                response.raise_for_status()
                return response.json()
            except Exception as exc:
                last_exc = exc
        raise last_exc or RuntimeError("embedding request failed")


def _embedding_api_key(provider: str) -> str:
    if provider == "dashscope":
        return os.getenv("EMBEDDING_API_KEY") or os.getenv("DASHSCOPE_API_KEY", "")
    return os.getenv("EMBEDDING_API_KEY") or os.getenv("OPENAI_API_KEY", "")


def _provider_default_model(provider: str, explicit_model: str | None = None) -> str:
    if explicit_model:
        return explicit_model
    env_model = os.getenv("EMBEDDING_MODEL", "").strip()
    if provider == "local":
        return env_model or "BAAI/bge-m3"
    if env_model and env_model != "BAAI/bge-m3":
        return env_model
    return "text-embedding-v4"


def _clean_embedding_text(text: str) -> str:
    cleaned = str(text or "").strip()
    return cleaned or " "


def _embedding_vector(item: Any) -> list[float]:
    raw = item.get("embedding") if isinstance(item, dict) else getattr(item, "embedding", None)
    if raw is None:
        raise EmbeddingProviderError("Embedding response item is missing an embedding vector.")
    return [float(value) for value in raw]


def _embedding_response_data(response: Any) -> list[Any]:
    if isinstance(response, dict):
        return list(response.get("data") or [])
    return list(getattr(response, "data", None) or [])


def create_embedding_provider(provider: str | None = None, **overrides: Any) -> BaseEmbeddingProvider:
    selected = (provider or os.getenv("EMBEDDING_PROVIDER") or "local").strip().lower()
    if selected in {"", "local"}:
        return LocalEmbeddingProvider(model=overrides.get("model"), dim=overrides.get("dim"))
    if selected in {"dashscope", "openai_compatible"}:
        return OpenAICompatibleEmbeddingProvider(
            provider=selected,
            model=overrides.get("model"),
            dim=overrides.get("dim"),
            base_url=overrides.get("base_url"),
            api_key=overrides.get("api_key"),
            batch_size=overrides.get("batch_size"),
            timeout_seconds=overrides.get("timeout_seconds"),
            max_retries=overrides.get("max_retries"),
            client=overrides.get("client"),
        )
    raise ValueError(f"Unsupported EMBEDDING_PROVIDER={selected!r}; expected local, dashscope, or openai_compatible.")


# 设计思路：
# 1. 本地密集向量使用 HuggingFaceEmbeddings，调用用户指定的模型（默认为 BAAI/bge-m3），支持 CPU 和 GPU。
# 2. 稀疏向量使用 BM25 方案，文本切词后计算 TF-IDF 权重，维度为词表大小。词表和文档频次统计持久化到本地 JSON 文件，支持增量更新。
# 3. 通过锁机制保证多线程环境下的统计更新和持久化安全。写入 Milvus 时先调用增量更新方法，确保 BM25 统计与 Milvus 中的稀疏向量维度一致。
# 4. 提供 get_all_embeddings 方法同时返回密集和稀疏向量，方便 MilvusWriter 一次性获取并写入。


def _create_dense_embedder() -> HuggingFaceEmbeddings:
    """向量化服务：封装 create dense embedder 相关逻辑，供上层流程复用。"""
    model_name = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
    # HuggingFaceEmbeddings 内部会根据 model_kwargs 中的 device 参数自动选择 CPU 或 GPU 进行推理，无需用户额外处理。
    # os.getenv("EMBEDDING_DEVICE") 可以设置为 "cpu"、"cuda" 或 "auto"，默认为 "cpu"。如果设置为 "auto"，HuggingFaceEmbeddings 会自动检测是否有可用的 GPU。
    device = os.getenv("EMBEDDING_DEVICE", "auto")
    return HuggingFaceEmbeddings(
        model_name=model_name,
        model_kwargs={"device": device},
        encode_kwargs={"normalize_embeddings": True},
    )



# 设计一个 EmbeddingService 类，封装密集向量和稀疏向量的生成逻辑，以及 BM25 统计的持久化和增量更新方法。
# 这样 MilvusWriter 和未来的检索服务都可以依赖同一个实例，确保统计数据的一致性和正确更新。
class EmbeddingService:
    """文本向量化服务 - 密集向量本地模型 + BM25 稀疏向量（持久化统计）"""

    def __init__(self, state_path: Path | str | None = None, provider: BaseEmbeddingProvider | None = None):
        """初始化对象状态，保存后续方法会复用的配置、连接或依赖实例。"""
        self.provider = provider or create_embedding_provider()
        self._embedder = None
        self._state_path = Path(state_path or os.getenv("BM25_STATE_PATH", _DEFAULT_STATE_PATH))# BM25 统计的持久化路径，默认为项目根目录下的 data/bm25_state.json
        self._lock = threading.Lock()# 用于保护 BM25 统计数据的线程安全更新

        # BM25 参数
        self.k1 = 1.5# BM25 中常用的 k1 参数，控制词频对权重的影响程度，通常取值在 1.2 到 2.0 之间。
        self.b = 0.75 # BM25 中常用的 b 参数，控制文档长度对权重的影响程度，通常取值在 0.5 到 0.8 之间。

        self._vocab: dict[str, int] = {}# 词表，key 是词，value 是对应的维度索引
        self._vocab_counter = 0
        self._doc_freq: Counter[str] = Counter()
        self._total_docs = 0
        self._sum_token_len = 0
        self._avg_doc_len = 1.0

        self._load_state()

    def _get_dense_embedder(self) -> HuggingFaceEmbeddings:
        if isinstance(self.provider, LocalEmbeddingProvider):
            return self.provider._get_embedder()
        raise EmbeddingProviderError(f"Embedding provider {self.provider.provider}/{self.provider.model} does not expose a local embedder.")

    @property
    def dim(self) -> int:
        return int(self.provider.dim)

    @property
    def provider_name(self) -> str:
        return self.provider.provider

    @property
    def model(self) -> str:
        return self.provider.model

# 重新计算平均文档长度，避免在增量更新过程中出现除以零的情况。
    def _recompute_avg_len(self) -> None:
        """向量化服务：封装 recompute avg len 相关逻辑，供上层流程复用。"""
        self._avg_doc_len = (
            self._sum_token_len / self._total_docs if self._total_docs > 0 else 1.0
        )

# 加载持久化的 BM25 统计数据，如果文件不存在或格式错误则初始化为空状态。
# 持久化是指将数据保存到非易失性存储介质（如磁盘文件）中，以便在程序重启后能够恢复之前的状态。
# 在这个 EmbeddingService 中，我们将 BM25 统计数据（词表、文档频次、总文档数、总词数）保存到一个 JSON 文件中。
# 当服务启动时，它会尝试加载这个文件以恢复之前的统计状态。如果文件不存在或格式不正确，则会初始化为一个空的统计状态。
    def _load_state(self) -> None:

        # 加载 BM25 统计数据的设计考虑：
        # 1. 文件路径：默认路径为项目根目录下的 data/bm25_state.json，用户可以通过环境变量 BM25_STATE_PATH 或构造函数参数 state_path 自定义路径。
        # 2. 文件格式：使用 JSON 格式存储，包含 version（版本控制）、vocab（词表）、doc_freq（文档频次）、total_docs（总文档数）、sum_token_len（总词数）等字段。
        # 3. 错误处理：如果文件不存在、无法解析或版本不兼容，服务会自动初始化为一个空的统计状态，确保不会因为持久化问题导致整个服务不可用。
        """向量化服务：读取 state 相关数据并转换成后续流程需要的结构。"""
        path = self._state_path
        if not path.is_file():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        
        # 版本控制：如果未来需要修改持久化格式，可以通过 version 字段进行兼容性判断，确保旧版本数据不会导致加载失败。
        if raw.get("version") != 1:
            return
        self._vocab = {str(k): int(v) for k, v in raw.get("vocab", {}).items()}# 词表数据，key 是词，value 是对应的维度索引
        self._doc_freq = Counter({str(k): int(v) for k, v in raw.get("doc_freq", {}).items()})# 文档频次数据，key 是词，value 是包含该词的文档数量
        self._total_docs = int(raw.get("total_docs", 0))# 总文档数量
        self._sum_token_len = int(raw.get("sum_token_len", 0))# 所有文档的总词数，用于计算平均文档长度
        if self._vocab:# 词表不为空时，更新 vocab_counter 以确保新词的维度索引不会与现有词冲突。
            self._vocab_counter = max(self._vocab.values()) + 1
        else:
            self._vocab_counter = 0
        self._recompute_avg_len()

    def _persist_unlocked(self) -> None:# 将当前的 BM25 统计数据持久化到 JSON 文件中。这个方法假设调用者已经获取了锁，确保在多线程环境下的安全性。
        """向量化服务：封装 persist unlocked 相关逻辑，供上层流程复用。"""
        self._state_path.parent.mkdir(parents=True, exist_ok=True)# 确保目录存在
        payload = {
            "version": 1,
            "total_docs": self._total_docs,
            "sum_token_len": self._sum_token_len,
            "vocab": self._vocab,
            "doc_freq": dict(self._doc_freq),
        }
        tmp = self._state_path.with_suffix(".json.tmp")# 先写入一个临时文件，写入完成后再替换原文件，避免在写入过程中出现数据损坏。 
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        for attempt in range(BM25_STATE_REPLACE_RETRIES):
            try:
                tmp.replace(self._state_path)
                return
            except PermissionError:
                if attempt + 1 == BM25_STATE_REPLACE_RETRIES:
                    raise
                time.sleep(BM25_STATE_REPLACE_RETRY_SECONDS)

    def _persist(self) -> None:# 获取锁后调用 _persist_unlocked 方法将 BM25 统计数据持久化到 JSON 文件中，确保在多线程环境下的安全性。
        """向量化服务：封装 persist 相关逻辑，供上层流程复用。"""
        with self._lock:
            self._persist_unlocked()

    def reset_state(self) -> None:
        """Clear BM25 statistics before a full corpus rebuild."""
        with self._lock:
            self._vocab = {}
            self._vocab_counter = 0
            self._doc_freq = Counter()
            self._total_docs = 0
            self._sum_token_len = 0
            self._avg_doc_len = 1.0
            self._persist_unlocked()

    def snapshot_state(self) -> dict[str, Any]:
        """Return a copy of persistent BM25 statistics for failure rollback."""

        with self._lock:
            return {
                "vocab": dict(self._vocab),
                "vocab_counter": self._vocab_counter,
                "doc_freq": Counter(self._doc_freq),
                "total_docs": self._total_docs,
                "sum_token_len": self._sum_token_len,
                "avg_doc_len": self._avg_doc_len,
            }

    def restore_state(self, snapshot: dict[str, Any]) -> None:
        """Restore and persist a state produced by :meth:`snapshot_state`."""

        with self._lock:
            self._vocab = dict(snapshot.get("vocab") or {})
            self._vocab_counter = int(snapshot.get("vocab_counter") or 0)
            self._doc_freq = Counter(snapshot.get("doc_freq") or {})
            self._total_docs = int(snapshot.get("total_docs") or 0)
            self._sum_token_len = int(snapshot.get("sum_token_len") or 0)
            self._avg_doc_len = float(snapshot.get("avg_doc_len") or 1.0)
            self._persist_unlocked()

    def increment_add_documents(self, texts: list[str]) -> None:
        """
        将每个 text 视为 BM25 中的一篇文档（与当前 chunk 写入粒度一致），增量更新 N / df / 长度和。
        """
        if not texts:
            return
        with self._lock:
            for text in texts:
                tokens = self.tokenize(text)
                doc_len = len(tokens)
                self._sum_token_len += doc_len
                self._total_docs += 1
                for token in set(tokens):
                    if token not in self._vocab:
                        self._vocab[token] = self._vocab_counter
                        self._vocab_counter += 1
                    self._doc_freq[token] += 1
            self._recompute_avg_len()
            self._persist_unlocked()

    def increment_remove_documents(self, texts: list[str]) -> None:
        """
        从语料统计中移除与 increment_add_documents 对称的文档集合（如删除某文件的全部 chunk 文本）。
        词表索引不回收，避免与 Milvus 中仍可能存在的旧稀疏向量维度冲突。
        """
        if not texts:
            return
        with self._lock:
            for text in texts:
                tokens = self.tokenize(text)
                doc_len = len(tokens)
                self._sum_token_len = max(0, self._sum_token_len - doc_len)
                self._total_docs = max(0, self._total_docs - 1)
                for token in set(tokens):
                    if token not in self._doc_freq:
                        continue
                    self._doc_freq[token] -= 1
                    if self._doc_freq[token] <= 0:
                        del self._doc_freq[token]
            self._recompute_avg_len()
            self._persist_unlocked()

    def get_embeddings(self, texts: list[str]) -> list[list[float]]:
        """向量化服务：获取 embeddings，屏蔽配置、缓存或外部依赖细节。"""
        if not texts:
            return []
        return self.provider.embed_texts(texts)

    def tokenize(self, text: str) -> list[str]:
        """向量化服务：封装 tokenize 相关逻辑，供上层流程复用。"""
        text = text.lower()
        tokens = []
        chinese_pattern = re.compile(r"[\u4e00-\u9fff]")
        ascii_pattern = re.compile(r"[a-z0-9]+")
        i = 0
        while i < len(text):
            char = text[i]
            if chinese_pattern.match(char):
                tokens.append(char)
                i += 1
            elif ascii_pattern.match(char):
                match = ascii_pattern.match(text[i:])
                if match:
                    tokens.append(match.group())
                    i += len(match.group())
            else:
                i += 1
        return tokens

    def _non_empty_sparse_vector_unlocked(self, sparse_vector: dict[int, float], *, update_vocab: bool = False) -> tuple[dict[int, float], bool]:
        """Return a Milvus-compatible sparse vector, adding a tiny fallback when needed."""
        if sparse_vector:
            return sparse_vector, False

        if _EMPTY_SPARSE_TOKEN not in self._vocab:
            if not update_vocab:
                return {0: _EMPTY_SPARSE_WEIGHT}, False
            self._vocab[_EMPTY_SPARSE_TOKEN] = self._vocab_counter
            self._vocab_counter += 1
            vocab_changed = True
        else:
            vocab_changed = False

        return {self._vocab[_EMPTY_SPARSE_TOKEN]: _EMPTY_SPARSE_WEIGHT}, vocab_changed

    def _sparse_vector_for_text_unlocked(self, text: str, *, update_vocab: bool = False) -> tuple[dict, bool]:
        """向量化服务：封装 sparse vector for text unlocked 相关逻辑，供上层流程复用。"""
        tokens = self.tokenize(text)
        doc_len = len(tokens)
        tf = Counter(tokens)
        sparse_vector: dict[int, float] = {}
        vocab_changed = False
        n = max(self._total_docs, 0)
        avg = max(self._avg_doc_len, 1.0)

        for token, freq in tf.items():
            if token not in self._vocab:
                if not update_vocab:
                    continue
                self._vocab[token] = self._vocab_counter
                self._vocab_counter += 1
                vocab_changed = True

            idx = self._vocab[token]
            df = self._doc_freq.get(token, 0)
            if df == 0:
                idf = math.log((n + 1) / 1)
            else:
                idf = math.log((n - df + 0.5) / (df + 0.5) + 1)

            numerator = freq * (self.k1 + 1)
            denominator = freq + self.k1 * (1 - self.b + self.b * doc_len / avg)
            score = idf * numerator / denominator
            if score > 0:
                sparse_vector[idx] = float(score)

        safe_sparse_vector, fallback_vocab_changed = self._non_empty_sparse_vector_unlocked(sparse_vector, update_vocab=update_vocab)
        return safe_sparse_vector, vocab_changed or fallback_vocab_changed

    def get_sparse_embedding(self, text: str) -> dict:
        """向量化服务：获取 sparse embedding，屏蔽配置、缓存或外部依赖细节。"""
        with self._lock:
            sparse_vector, vocab_changed = self._sparse_vector_for_text_unlocked(text)
            if vocab_changed:
                self._persist_unlocked()
        return sparse_vector

    def get_sparse_embeddings(self, texts: list[str]) -> list[dict]:
        """向量化服务：获取 sparse embeddings，屏蔽配置、缓存或外部依赖细节。"""
        if not texts:
            return []
        with self._lock:
            out: list[dict] = []
            any_new_vocab = False
            for text in texts:
                sparse_vector, vocab_changed = self._sparse_vector_for_text_unlocked(text)
                out.append(sparse_vector)
                any_new_vocab = any_new_vocab or vocab_changed
            if any_new_vocab:
                self._persist_unlocked()
        return out

    def get_all_embeddings(self, texts: list[str]) -> tuple[list[list[float]], list[dict]]:
        """向量化服务：获取 all embeddings，屏蔽配置、缓存或外部依赖细节。"""
        dense_embeddings = self.get_embeddings(texts)
        sparse_embeddings = self.get_sparse_embeddings(texts)
        return dense_embeddings, sparse_embeddings


_embedding_service_instance: EmbeddingService | None = None


def get_embedding_service() -> EmbeddingService:
    """Return the process-wide embedding service, creating the local model lazily."""

    global _embedding_service_instance
    if _embedding_service_instance is None:
        _embedding_service_instance = EmbeddingService()
    return _embedding_service_instance


class _LazyEmbeddingService:
    """Compatibility proxy for modules that import embedding_service directly."""

    def __getattr__(self, name: str):
        return getattr(get_embedding_service(), name)


# 全进程唯一实例代理：写入与检索共用同一份 BM25 持久化状态。
# 密集向量模型按需加载，避免 API 启动或 Milvus 管理脚本在缺少 torch 时导入失败。
embedding_service = _LazyEmbeddingService()

