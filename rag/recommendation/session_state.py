"""Small persistence boundary for V3 SessionCore.

Business state is typed and owned by :mod:`rag.recommendation.v3.session`.
This module only selects Redis or in-memory storage and serializes the one
transport envelope.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import os
import threading
import time
from typing import Dict, Optional, Protocol


DEFAULT_SESSION_TTL_SECONDS = 7200
SESSION_KEY_PREFIX = "mallmind:v3:session:"


@dataclass
class ShoppingSession:
    session_id: str
    updated_at: float = field(default_factory=time.time)
    v3_core: dict[str, object] = field(default_factory=dict)


class SessionStore(Protocol):
    def get(self, session_id: str) -> Optional[ShoppingSession]: ...
    def save(self, session: ShoppingSession) -> None: ...
    def delete(self, session_id: str) -> None: ...


class InMemorySessionStore:
    def __init__(self) -> None:
        self._items: Dict[str, ShoppingSession] = {}
        self._lock = threading.RLock()

    def get(self, session_id: str) -> Optional[ShoppingSession]:
        with self._lock:
            return self._items.get(session_id)

    def save(self, session: ShoppingSession) -> None:
        with self._lock:
            self._items[session.session_id] = session

    def delete(self, session_id: str) -> None:
        with self._lock:
            self._items.pop(session_id, None)


class RedisSessionStore:
    def __init__(self, redis_url: str, ttl_seconds: int) -> None:
        import redis

        self._client = redis.Redis.from_url(redis_url, decode_responses=True)
        self._client.ping()
        self._ttl_seconds = ttl_seconds

    def get(self, session_id: str) -> Optional[ShoppingSession]:
        raw = self._client.get(self._key(session_id))
        if not raw:
            return None
        try:
            data = json.loads(raw)
            core = data.get("v3_core")
            if not isinstance(core, dict):
                raise ValueError("v3_core must be an object")
            return ShoppingSession(session_id=str(data["session_id"]), updated_at=float(data.get("updated_at", time.time())), v3_core=core)
        except (TypeError, ValueError, KeyError, json.JSONDecodeError):
            self.delete(session_id)
            return None

    def save(self, session: ShoppingSession) -> None:
        payload = json.dumps(asdict(session), ensure_ascii=False, separators=(",", ":"))
        self._client.setex(self._key(session.session_id), self._ttl_seconds, payload)

    def delete(self, session_id: str) -> None:
        self._client.delete(self._key(session_id))

    @staticmethod
    def _key(session_id: str) -> str:
        return f"{SESSION_KEY_PREFIX}{session_id}"


_memory_store = InMemorySessionStore()
_store: SessionStore = _memory_store
_store_config: tuple[str, str, int] = ("memory", "", DEFAULT_SESSION_TTL_SECONDS)


def get_session(session_id: Optional[str]) -> ShoppingSession:
    key = str(session_id or "").strip()
    if not key:
        raise ValueError("session_id is required")
    store = get_session_store()
    session = store.get(key) or ShoppingSession(session_id=key)
    session.updated_at = time.time()
    store.save(session)
    return session


def save_session(session: ShoppingSession) -> None:
    session.updated_at = time.time()
    get_session_store().save(session)


def get_session_store() -> SessionStore:
    global _store, _store_config
    backend = os.getenv("SESSION_BACKEND", "memory").strip().lower()
    redis_url = os.getenv("REDIS_URL", "").strip()
    ttl = _positive_int(os.getenv("SESSION_TTL_SECONDS"), DEFAULT_SESSION_TTL_SECONDS)
    config = (backend, redis_url, ttl)
    if config == _store_config:
        return _store
    if backend == "memory":
        _store = _memory_store
    elif backend == "redis":
        if not redis_url:
            raise RuntimeError("SESSION_BACKEND=redis requires REDIS_URL")
        _store = RedisSessionStore(redis_url, ttl)
    else:
        raise RuntimeError(f"Unsupported SESSION_BACKEND: {backend}")
    _store_config = config
    return _store


def _positive_int(raw: Optional[str], default: int) -> int:
    try:
        value = int(str(raw or default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default
