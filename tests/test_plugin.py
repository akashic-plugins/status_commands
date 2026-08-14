from __future__ import annotations

import hashlib
from pathlib import Path
from typing import cast

import pytest
from agent.plugin_composition import (
    COMMANDS,
    SESSION_READ,
    UI_SLOTS,
    CommandResult,
    CompositionRoot,
    Context,
    MobileUiRpcInvalidRequest,
    PluginCommands,
    PluginRuntime,
    PluginUiSlots,
    SessionReadService,
)
from session.manager import SessionManager

import status_commands_source.plugin as plugin_module
from status_commands_source.plugin import (
    _build_memory_status_projection,
    _format_memory_status_reply,
    _mobile_memory_status_query,
)


class _SessionFixture:
    def __init__(
        self,
        messages: list[dict[str, object]],
        last_consolidated: int,
    ) -> None:
        self.messages = messages
        self.last_consolidated = last_consolidated


@pytest.mark.asyncio
async def test_v3_apply_registers_memory_only_command_and_mobile_ui(
    tmp_path: Path,
) -> None:
    session = _SessionFixture(
        messages=[
            {"role": "user", "content": "已整理的问题"},
            {"role": "assistant", "content": "旧回答"},
            {"role": "user", "content": "待整理的问题"},
        ],
        last_consolidated=2,
    )
    requested: list[str] = []

    def get_existing(session_key: str) -> _SessionFixture:
        requested.append(session_key)
        if session_key == "mobile:existing":
            return session
        raise KeyError(session_key)

    root = CompositionRoot("status-commands-v3")
    commands = PluginCommands()
    ui_slots = PluginUiSlots()
    _ = await root.context.provide(COMMANDS, commands)
    _ = await root.context.provide(
        SESSION_READ,
        SessionReadService(get_existing),
    )
    _ = await root.context.provide(UI_SLOTS, ui_slots)

    async def mount_plugin(ctx: Context) -> None:
        await plugin_module.apply(ctx, {})

    plugin_file = plugin_module.__file__
    assert plugin_file is not None
    plugin_dir = Path(plugin_file).resolve().parent
    _ = await root.mount(
        mount_plugin,
        name="status_commands",
        inject=plugin_module.inject,
        runtime=PluginRuntime(
            plugin_id="status_commands",
            plugin_dir=plugin_dir,
            data_dir=tmp_path / "plugin-data",
            workspace=tmp_path / "workspace",
            config={},
        ),
    )

    registry = commands.freeze()
    assert [item.name for item in registry.descriptors] == ["memorystatus"]
    execution = await registry.execute(
        "/memory_status",
        session_key="mobile:existing",
        channel="mobile",
        chat_id="existing",
        sender="hua",
    )
    assert execution is not None
    assert execution.result == CommandResult(
        "success",
        _format_memory_status_reply(
            _build_memory_status_projection(session.messages, 2)
        ),
    )
    assert (
        await registry.execute(
            "/kvcache",
            session_key="mobile:existing",
            channel="mobile",
            chat_id="existing",
            sender="hua",
        )
        is None
    )

    contribution = ui_slots.freeze()["status_commands"]
    assert contribution.mobile_ui_asset is not None
    assert contribution.mobile_ui_asset.slots == ("drawer.panel",)
    query = contribution.mobile_ui_query
    assert query is not None
    mobile_result = cast(
        dict[str, object],
        query(
            "memory.status",
            {},
            session_id="mobile:existing",
            turn_id=None,
        ),
    )
    assert mobile_result["state"] == "pending"
    assert mobile_result["pending_user_messages"] == 1
    unavailable = cast(
        dict[str, object],
        query(
            "memory.status",
            {},
            session_id="mobile:deleted",
            turn_id=None,
        ),
    )
    assert unavailable == {
        "state": "unavailable",
        "summary": "电脑端已不存在",
        "pending_user_messages": 0,
        "message_count": 0,
        "last_consolidated_preview": None,
    }
    with pytest.raises(MobileUiRpcInvalidRequest, match="缺少 session_id"):
        _ = query("memory.status", {}, session_id=None, turn_id=None)
    with pytest.raises(
        MobileUiRpcInvalidRequest,
        match="未知 status_commands 移动方法",
    ):
        _ = query(
            "kvcache.overview",
            {},
            session_id="mobile:existing",
            turn_id=None,
        )

    missing = await registry.execute(
        "/compact_status",
        session_key="mobile:missing",
        channel="mobile",
        chat_id="missing",
        sender="hua",
    )
    assert missing is not None
    assert "当前会话不存在" in missing.result.text
    assert requested == [
        "mobile:existing",
        "mobile:existing",
        "mobile:deleted",
        "mobile:missing",
    ]

    await root.dispose()
    assert root.receipt().effects == ()


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


def test_mobile_memory_status_keeps_session_database_unchanged(
    tmp_path: Path,
) -> None:
    manager = SessionManager(tmp_path)
    try:
        session = manager.get_or_create("mobile:readonly")
        session.messages = [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
        ]
        manager.save(session)
        manager.invalidate(session.key)
        before = _session_database_snapshot(tmp_path)

        result = _mobile_memory_status_query(
            SessionReadService(manager.get_existing),
            "memory.status",
            {},
            session_id=session.key,
            turn_id=None,
        )

        assert result["state"] == "never"
        assert _session_database_snapshot(tmp_path) == before
    finally:
        manager.close()


def _session_database_snapshot(
    workspace: Path,
) -> dict[str, tuple[int, int, str]]:
    return {
        path.name: (
            path.stat().st_size,
            path.stat().st_mtime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in workspace.glob("sessions.db*")
        if path.is_file()
    }
