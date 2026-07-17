from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime
from types import SimpleNamespace

import pytest
from session.manager import SessionManager

from status_commands_source.plugin import (
    KVCacheCommandModule,
    MemoryStatusCommandModule,
    StatusCommands,
    _build_memory_status_projection,
    _format_memory_status_reply,
)


@pytest.mark.asyncio
async def test_memory_status_command_aborts_turn() -> None:
    session = SimpleNamespace(
        messages=[
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
        ],
        last_consolidated=0,
    )
    state = SimpleNamespace(
        session_key="telegram:1",
        session=session,
        msg=SimpleNamespace(
            content="/memorystatus",
            channel="telegram",
            chat_id="1",
            timestamp=datetime.now(),
        ),
    )
    frame = SimpleNamespace(input=state, slots={"session:session": session})
    await MemoryStatusCommandModule("status_commands").run(frame)
    assert frame.slots["session:ctx"].abort is True


def test_status_commands_only_owns_memory_mobile_surface() -> None:
    assert StatusCommands.dashboard_module() is None
    assert StatusCommands.mobile_ui_module() == "mobile_panel.js"
    assert StatusCommands.mobile_ui_stylesheet() == "mobile_panel.css"


def test_memory_projection_is_shared_with_command_reply() -> None:
    projection = _build_memory_status_projection(
        [
            {"role": "user", "content": "已整理的问题"},
            {"role": "assistant", "content": "旧回答"},
            {"role": "user", "content": "待整理的问题"},
            {"role": "assistant", "content": "新回答"},
        ],
        2,
    )
    assert projection == {
        "state": "pending",
        "summary": "有 1 条消息待整理",
        "pending_user_messages": 1,
        "message_count": 4,
        "last_consolidated_preview": "已整理的问题",
    }
    reply = _format_memory_status_reply(projection)
    assert "尚未整理的用户消息数：1" in reply
    assert "“已整理的问题”" in reply


def test_memory_projection_ignores_context_frames() -> None:
    projection = _build_memory_status_projection(
        [
            {"role": "user", "content": "[Context Frame]\ninternal"},
            {"role": "user", "content": "真实问题"},
            {"role": "assistant", "content": "回答"},
        ],
        99,
    )
    assert projection["state"] == "up_to_date"
    assert projection["pending_user_messages"] == 0
    assert projection["last_consolidated_preview"] == "真实问题"


@pytest.mark.asyncio
async def test_mobile_memory_status_reads_existing_session_only() -> None:
    session = SimpleNamespace(
        messages=[
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
        ],
        last_consolidated=2,
    )

    class SessionManager:
        def __init__(self) -> None:
            self.requested = []

        def _load(self, key: str):
            self.requested.append(key)
            return session

        def get_or_create(self, key: str):
            raise AssertionError(f"状态查询不得创建会话: {key}")

    manager = SessionManager()
    plugin = StatusCommands()
    plugin.context = SimpleNamespace(session_manager=manager)
    result = await plugin.mobile_ui_call(
        "memory.status",
        {},
        session_id="mobile:existing",
        turn_id=None,
    )
    assert manager.requested == ["mobile:existing"]
    assert result["state"] == "up_to_date"
    assert result["message_count"] == 2


@pytest.mark.asyncio
async def test_mobile_memory_status_rejects_missing_session() -> None:
    plugin = StatusCommands()
    plugin.context = SimpleNamespace(session_manager=SimpleNamespace())
    with pytest.raises(ValueError, match="缺少 session_id"):
        await plugin.mobile_ui_call(
            "memory.status",
            {},
            session_id=None,
            turn_id=None,
        )

    with pytest.raises(ValueError, match="未知 status_commands 移动方法"):
        await plugin.mobile_ui_call(
            "kvcache.overview",
            {},
            session_id="mobile:existing",
            turn_id=None,
        )


@pytest.mark.asyncio
async def test_mobile_memory_status_does_not_recreate_deleted_session() -> None:
    class SessionManager:
        def _load(self, key: str):
            return None

        def get_or_create(self, key: str):
            raise AssertionError(f"状态查询不得创建会话: {key}")

    plugin = StatusCommands()
    plugin.context = SimpleNamespace(session_manager=SessionManager())
    result = await plugin.mobile_ui_call(
        "memory.status",
        {},
        session_id="mobile:deleted",
        turn_id=None,
    )
    assert result == {
        "state": "unavailable",
        "summary": "电脑端已不存在",
        "pending_user_messages": 0,
        "message_count": 0,
        "last_consolidated_preview": None,
    }


@pytest.mark.asyncio
async def test_mobile_memory_status_keeps_session_database_unchanged(tmp_path) -> None:
    manager = SessionManager(tmp_path)
    try:
        session = manager.get_or_create("mobile:readonly")
        session.messages = [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
        ]
        session.last_consolidated = 2
        manager.save(session)
        manager.invalidate(session.key)
        before = _session_database_snapshot(tmp_path)

        plugin = StatusCommands()
        plugin.context = SimpleNamespace(session_manager=manager)
        result = await plugin.mobile_ui_call(
            "memory.status",
            {},
            session_id=session.key,
            turn_id=None,
        )

        assert result["state"] == "up_to_date"
        assert _session_database_snapshot(tmp_path) == before
    finally:
        manager.close()


@pytest.mark.asyncio
async def test_kvcache_command_reads_observe_db(tmp_path) -> None:
    observe_dir = tmp_path / "observe"
    observe_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(observe_dir / "observe.db")
    try:
        conn.execute(
            """
            CREATE TABLE turns(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                source TEXT NOT NULL,
                session_key TEXT NOT NULL,
                user_msg TEXT,
                llm_output TEXT NOT NULL DEFAULT '',
                react_cache_prompt_tokens INTEGER,
                react_cache_hit_tokens INTEGER
            )
            """
        )
        conn.execute(
            """
            INSERT INTO turns(
                ts, source, session_key, user_msg, llm_output,
                react_cache_prompt_tokens, react_cache_hit_tokens
            ) VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-04-19T03:20:00+00:00",
                "agent",
                "telegram:100",
                "again",
                "ok",
                300,
                260,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    state = SimpleNamespace(
        session_key="telegram:100",
        msg=SimpleNamespace(content="/kvcache", channel="telegram", chat_id="100"),
    )
    reply = KVCacheCommandModule(
        "status_commands",
        observe_dir / "observe.db",
    )._build_reply(state)
    assert "KVCache" in reply
    assert "260 / 300" in reply


def _session_database_snapshot(workspace) -> dict[str, tuple[int, int, str]]:
    return {
        path.name: (
            path.stat().st_size,
            path.stat().st_mtime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in workspace.glob("sessions.db*")
        if path.is_file()
    }
