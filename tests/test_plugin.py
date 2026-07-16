from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
import sqlite3

import pytest

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


def test_status_commands_does_not_own_visual_surfaces() -> None:
    assert StatusCommands.dashboard_module() is None
    assert StatusCommands.mobile_ui_module() is None
    assert StatusCommands.mobile_ui_stylesheet() is None


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
