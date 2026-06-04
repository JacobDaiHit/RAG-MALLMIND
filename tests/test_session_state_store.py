import pytest

import rag.recommendation.session_state as session_state
from rag.recommendation.session_state import (
    CartItem,
    InMemorySessionStore,
    RedisSessionStore,
    ShoppingSession,
    cleanup_expired_sessions,
    get_session,
    remember_pc_build_plan,
    reset_session,
    save_session,
)


def setup_function():
    session_state._SESSIONS.clear()
    session_state._SESSION_STORE = InMemorySessionStore(session_state._SESSIONS)
    session_state._SESSION_STORE_CONFIG = ("memory", "", session_state.DEFAULT_SESSION_TTL_SECONDS)
    session_state._LAST_SESSION_CLEANUP_AT = 0.0


def test_memory_backend_get_session_creates_and_reuses_state(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("SESSION_BACKEND", "memory")

    session = get_session("refresh-me")
    session.last_goal = "find headphones"
    save_session(session)

    same_session = get_session("refresh-me")

    assert same_session is session
    assert same_session.last_goal == "find headphones"
    assert same_session.updated_at >= session.updated_at


def test_reset_session_deletes_and_rebuilds_state(monkeypatch):
    monkeypatch.setenv("SESSION_BACKEND", "memory")
    session = get_session("reset-me")
    session.last_goal = "old goal"
    save_session(session)

    rebuilt = reset_session("reset-me")

    assert rebuilt.session_id == "reset-me"
    assert rebuilt is get_session("reset-me")
    assert rebuilt.last_goal == ""
    assert rebuilt.topic_memory["source"] == "init"


def test_session_modification_save_and_get_restores_nested_state():
    store = InMemorySessionStore({})
    session_state._SESSION_STORE = store
    session_state._SESSION_STORE_CONFIG = ("memory", "", session_state.DEFAULT_SESSION_TTL_SECONDS)

    session = ShoppingSession(session_id="stateful")
    session.last_goal = "gaming pc"
    session.last_result = {"type": "pc_build_plan", "items": [{"product_id": "pc_cpu_1"}]}
    session.cart["pc_cpu_1"] = CartItem(product_id="pc_cpu_1", quantity=2)
    session.pc_build_history.append({"summary": "balanced build"})
    session.topic_memory = {"topic_type": "pc_build", "slots": {"budget": 8000}}
    save_session(session)

    restored = get_session("stateful")

    assert restored.last_goal == "gaming pc"
    assert restored.last_result["type"] == "pc_build_plan"
    assert restored.cart["pc_cpu_1"].quantity == 2
    assert restored.pc_build_history[0]["summary"] == "balanced build"
    assert restored.topic_memory["slots"]["budget"] == 8000


def test_cleanup_expired_sessions_removes_old_sessions(monkeypatch):
    monkeypatch.setenv("SESSION_TTL_SECONDS", "10")
    monkeypatch.setenv("SESSION_MAX_COUNT", "10")
    session_state._SESSIONS["old"] = ShoppingSession(session_id="old", updated_at=1.0)
    session_state._SESSIONS["fresh"] = ShoppingSession(session_id="fresh", updated_at=95.0)

    removed = cleanup_expired_sessions(now=100.0)

    assert removed == 1
    assert "old" not in session_state._SESSIONS
    assert "fresh" in session_state._SESSIONS


def test_cleanup_expired_sessions_limits_capacity(monkeypatch):
    monkeypatch.setenv("SESSION_TTL_SECONDS", "1000")
    monkeypatch.setenv("SESSION_MAX_COUNT", "2")
    session_state._SESSIONS["oldest"] = ShoppingSession(session_id="oldest", updated_at=1.0)
    session_state._SESSIONS["middle"] = ShoppingSession(session_id="middle", updated_at=2.0)
    session_state._SESSIONS["newest"] = ShoppingSession(session_id="newest", updated_at=3.0)

    removed = cleanup_expired_sessions(now=4.0)

    assert removed == 1
    assert list(session_state._SESSIONS) == ["middle", "newest"]


def test_get_session_enforces_capacity_after_creating_session(monkeypatch):
    monkeypatch.setenv("SESSION_TTL_SECONDS", "1000")
    monkeypatch.setenv("SESSION_MAX_COUNT", "2")
    session_state._SESSIONS["oldest"] = ShoppingSession(session_id="oldest", updated_at=1.0)
    session_state._SESSIONS["middle"] = ShoppingSession(session_id="middle", updated_at=2.0)
    monkeypatch.setattr(session_state, "_now_seconds", lambda: 100.0)

    session = get_session("newest")

    assert session.session_id == "newest"
    assert len(session_state._SESSIONS) == 2
    assert "oldest" not in session_state._SESSIONS
    assert "newest" in session_state._SESSIONS


def test_production_missing_session_id_does_not_use_default(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SESSION_BACKEND", "memory")

    with pytest.raises(ValueError, match="session_id is required"):
        get_session(None)

    assert "default" not in session_state._SESSIONS


def test_production_redis_backend_requires_redis_url(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SESSION_BACKEND", "redis")
    monkeypatch.delenv("REDIS_URL", raising=False)

    with pytest.raises(RuntimeError, match="REDIS_URL"):
        session_state.get_session_store()


def test_development_redis_backend_can_fallback_to_memory(monkeypatch, caplog):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("SESSION_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

    class BrokenRedisStore:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("redis unavailable")

    monkeypatch.setattr(session_state, "RedisSessionStore", BrokenRedisStore)

    store = session_state.get_session_store()

    assert isinstance(store, InMemorySessionStore)
    assert "Falling back to in-memory session store" in caplog.text


class FakeRedisClient:
    def __init__(self):
        self.values = {}
        self.ttls = {}

    def ping(self):
        return True

    def get(self, key):
        return self.values.get(key)

    def setex(self, key, ttl, value):
        self.values[key] = value
        self.ttls[key] = ttl

    def set(self, key, value):
        self.values[key] = value

    def delete(self, key):
        self.values.pop(key, None)


def redis_store_with_fake_client(fake):
    store = RedisSessionStore.__new__(RedisSessionStore)
    store.redis_url = "redis://fake/0"
    store.ttl_seconds = session_state.DEFAULT_SESSION_TTL_SECONDS
    store._client = fake
    return store


def test_redis_session_store_serializes_and_deserializes_nested_fields(monkeypatch):
    monkeypatch.setenv("SESSION_TTL_SECONDS", "123")
    fake = FakeRedisClient()
    store = redis_store_with_fake_client(fake)
    session = ShoppingSession(session_id="redis-one")

    remember_pc_build_plan(session, "build a pc", {"type": "pc_build_plan", "parts": [{"product_id": "pc_gpu_1"}]})
    session.cart["pc_gpu_1"] = CartItem(product_id="pc_gpu_1", quantity=1)
    session.topic_memory = {"topic_type": "pc_build", "slots": {"budget": 6000}}
    store.save(session)

    restored = store.get("redis-one")

    assert restored is not None
    assert restored.last_goal == "build a pc"
    assert restored.last_result["type"] == "pc_build_plan"
    assert restored.pc_build_history[0]["parts"][0]["product_id"] == "pc_gpu_1"
    assert restored.cart["pc_gpu_1"].quantity == 1
    assert restored.topic_memory["slots"]["budget"] == 6000
    assert fake.ttls["mallmind:session:redis-one"] == 123


def test_redis_session_store_deletes_corrupt_payload(caplog):
    fake = FakeRedisClient()
    fake.values["mallmind:session:broken"] = "{not-json"
    store = redis_store_with_fake_client(fake)

    assert store.get("broken") is None
    assert "mallmind:session:broken" not in fake.values
    assert "Session deserialization failed" in caplog.text
