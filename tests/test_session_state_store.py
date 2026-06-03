import rag.recommendation.session_state as session_state
from rag.recommendation.session_state import ShoppingSession, cleanup_expired_sessions, get_session


def setup_function():
    session_state._SESSIONS.clear()
    session_state._LAST_SESSION_CLEANUP_AT = 0.0


def test_get_session_refreshes_updated_at(monkeypatch):
    times = iter([100.0, 101.0, 110.0, 111.0])
    monkeypatch.setattr(session_state, "_now_seconds", lambda: next(times))

    session = get_session("refresh-me")
    first_updated_at = session.updated_at
    same_session = get_session("refresh-me")

    assert same_session is session
    assert first_updated_at == 101.0
    assert same_session.updated_at == 111.0


def test_cleanup_expired_sessions_removes_old_sessions(monkeypatch):
    monkeypatch.setenv("SESSION_TTL_SECONDS", "10")
    monkeypatch.setenv("MAX_IN_MEMORY_SESSIONS", "10")
    session_state._SESSIONS["old"] = ShoppingSession(session_id="old", updated_at=1.0)
    session_state._SESSIONS["fresh"] = ShoppingSession(session_id="fresh", updated_at=95.0)

    removed = cleanup_expired_sessions(now=100.0)

    assert removed == 1
    assert "old" not in session_state._SESSIONS
    assert "fresh" in session_state._SESSIONS


def test_cleanup_expired_sessions_limits_capacity(monkeypatch):
    monkeypatch.setenv("SESSION_TTL_SECONDS", "1000")
    monkeypatch.setenv("MAX_IN_MEMORY_SESSIONS", "2")
    session_state._SESSIONS["oldest"] = ShoppingSession(session_id="oldest", updated_at=1.0)
    session_state._SESSIONS["middle"] = ShoppingSession(session_id="middle", updated_at=2.0)
    session_state._SESSIONS["newest"] = ShoppingSession(session_id="newest", updated_at=3.0)

    removed = cleanup_expired_sessions(now=4.0)

    assert removed == 1
    assert list(session_state._SESSIONS) == ["middle", "newest"]


def test_get_session_enforces_capacity_after_creating_session(monkeypatch):
    monkeypatch.setenv("SESSION_TTL_SECONDS", "1000")
    monkeypatch.setenv("MAX_IN_MEMORY_SESSIONS", "2")
    session_state._SESSIONS["oldest"] = ShoppingSession(session_id="oldest", updated_at=1.0)
    session_state._SESSIONS["middle"] = ShoppingSession(session_id="middle", updated_at=2.0)
    monkeypatch.setattr(session_state, "_now_seconds", lambda: 100.0)

    session = get_session("newest")

    assert session.session_id == "newest"
    assert len(session_state._SESSIONS) == 2
    assert "oldest" not in session_state._SESSIONS
    assert "newest" in session_state._SESSIONS
