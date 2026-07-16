from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
import sqlite3

import pytest

from status_commands_source.kvcache_reader import KVCacheDashboardReader
from status_commands_source.plugin import (
    KVCacheCommandModule,
    MemoryStatusCommandModule,
    StatusCommands,
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
    reply = KVCacheCommandModule("status_commands", observe_dir / "observe.db")._build_reply(state)
    assert "KVCache" in reply
    assert "260 / 300" in reply


def test_kvcache_dashboard_reader_summarizes_workspace_db(tmp_path) -> None:
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
    summary = KVCacheDashboardReader(tmp_path).get_summary()
    assert summary["tracked_turn_count"] == 1
    assert summary["hit_tokens"] == 260


@pytest.mark.asyncio
async def test_mobile_kvcache_rpc_reuses_dashboard_projection(tmp_path) -> None:
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
                react_cache_prompt_tokens INTEGER,
                react_cache_hit_tokens INTEGER
            )
            """
        )
        conn.execute(
            """
            INSERT INTO turns(
                ts, source, session_key, user_msg,
                react_cache_prompt_tokens, react_cache_hit_tokens
            ) VALUES(?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-07-16T12:00:00+00:00",
                "proactive",
                "mobile:test",
                "真实 Turn",
                1_000,
                880,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    plugin = StatusCommands()
    plugin.context = SimpleNamespace(workspace=tmp_path)

    overview = await plugin.mobile_ui_call(
        "kvcache.overview",
        {},
        session_id=None,
        turn_id=None,
    )
    page = await plugin.mobile_ui_call(
        "kvcache.turns",
        {"page": 1, "page_size": 25},
        session_id=None,
        turn_id=None,
    )

    assert overview["tracked_turn_count"] == 1
    assert overview["proactive"]["hit_rate"] == pytest.approx(0.88)
    assert page["total"] == 1
    assert page["items"][0] == {
        "id": 1,
        "ts": "2026-07-16T12:00:00+00:00",
        "source": "proactive",
        "session_key": "mobile:test",
        "user_preview": "真实 Turn",
        "prompt_tokens": 1_000,
        "hit_tokens": 880,
        "miss_tokens": 120,
        "hit_rate": 0.88,
    }


@pytest.mark.asyncio
async def test_mobile_kvcache_passive_page_filters_before_limit(tmp_path) -> None:
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
                react_cache_prompt_tokens INTEGER,
                react_cache_hit_tokens INTEGER
            )
            """
        )
        agent_rows = [
            (
                f"2026-07-15T12:{minute:02d}:00+00:00",
                "agent",
                "mobile:passive",
                f"被动 {minute}",
                100,
                50 + minute,
            )
            for minute in range(12)
        ]
        active_rows = [
            (
                f"2026-07-16T12:{minute % 60:02d}:{minute // 60:02d}+00:00",
                "proactive" if minute % 2 == 0 else "drift",
                "mobile:active",
                f"主动 {minute}",
                100,
                80,
            )
            for minute in range(60)
        ]
        conn.executemany(
            """
            INSERT INTO turns(
                ts, source, session_key, user_msg,
                react_cache_prompt_tokens, react_cache_hit_tokens
            ) VALUES(?, ?, ?, ?, ?, ?)
            """,
            agent_rows + active_rows,
        )
        conn.commit()
    finally:
        conn.close()
    plugin = StatusCommands()
    plugin.context = SimpleNamespace(workspace=tmp_path)

    global_page = await plugin.mobile_ui_call(
        "kvcache.turns",
        {"page": 1, "page_size": 50},
        session_id=None,
        turn_id=None,
    )
    passive_page = await plugin.mobile_ui_call(
        "kvcache.turns",
        {"page": 1, "page_size": 10, "source": "agent"},
        session_id=None,
        turn_id=None,
    )
    passive_page_two = await plugin.mobile_ui_call(
        "kvcache.turns",
        {"page": 2, "page_size": 10, "source": "agent"},
        session_id=None,
        turn_id=None,
    )

    assert all(item["source"] != "agent" for item in global_page["items"])
    assert passive_page["total"] == 12
    assert [item["user_preview"] for item in passive_page["items"]] == [
        f"被动 {minute}" for minute in range(11, 1, -1)
    ]
    recent_prompt = sum(item["prompt_tokens"] for item in passive_page["items"])
    recent_hit = sum(item["hit_tokens"] for item in passive_page["items"])
    assert recent_hit / recent_prompt == pytest.approx(0.565)
    assert [item["user_preview"] for item in passive_page_two["items"]] == [
        "被动 1",
        "被动 0",
    ]


@pytest.mark.asyncio
async def test_mobile_kvcache_rpc_rejects_invalid_pagination(tmp_path) -> None:
    plugin = StatusCommands()
    plugin.context = SimpleNamespace(workspace=tmp_path)

    with pytest.raises(ValueError, match="page_size"):
        await plugin.mobile_ui_call(
            "kvcache.turns",
            {"page_size": 0},
            session_id=None,
            turn_id=None,
        )
    with pytest.raises(ValueError, match="source"):
        await plugin.mobile_ui_call(
            "kvcache.turns",
            {"source": "proactive"},
            session_id=None,
            turn_id=None,
        )
