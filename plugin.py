from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Literal, TypedDict, cast

from agent.plugin_composition import (
    COMMANDS,
    SESSION_READ,
    UI_SLOTS,
    CommandDefinition,
    CommandInvocation,
    CommandResult,
    Context,
    MobileUiDefinition,
    MobileUiRpcInvalidRequest,
    SessionReadService,
)
from agent.prompting import is_context_frame

logger = logging.getLogger("plugin.status_commands")

api_version = 3
name = "status_commands"
version = "2.0.0"
inject = (COMMANDS, SESSION_READ, UI_SLOTS)


class MemoryStatusProjection(TypedDict):
    state: Literal["never", "pending", "up_to_date", "unavailable"]
    summary: str
    pending_user_messages: int
    message_count: int
    last_consolidated_preview: str | None


async def apply(ctx: Context, config: object) -> None:
    """登记记忆状态命令和移动端只读界面。"""

    # 1. 只取得命令和界面共同依赖的脱离 Session 快照
    del config
    session_read = ctx.require(SESSION_READ)

    async def handle_memory_status(
        invocation: CommandInvocation,
    ) -> CommandResult:
        projection = _read_memory_status(session_read, invocation.session_key)
        logger.info("[status_commands] 命中命令: /%s", invocation.name)
        return CommandResult("success", _format_memory_status_reply(projection))

    def query_memory_status(
        method: str,
        payload: dict[str, object],
        *,
        session_id: str | None,
        turn_id: str | None,
    ) -> dict[str, object]:
        return _mobile_memory_status_query(
            session_read,
            method,
            payload,
            session_id=session_id,
            turn_id=turn_id,
        )

    # 2. Command 描述、别名和执行都归插件 Fiber 所有
    await ctx.require(COMMANDS).register(
        ctx,
        CommandDefinition(
            name="memorystatus",
            description="查看记忆整理状态",
            aliases=("memory_status", "compact_status"),
            handler=handle_memory_status,
        ),
    )

    # 3. Mobile UI 资产与查询处理器作为同一个 generation Effect 发布
    await ctx.require(UI_SLOTS).register_mobile(
        ctx,
        MobileUiDefinition(
            module="mobile_panel.js",
            stylesheet="mobile_panel.css",
            slots=("drawer.panel",),
        ),
        query=query_memory_status,
    )


def _read_memory_status(
    session_read: SessionReadService,
    session_key: str,
) -> MemoryStatusProjection:
    snapshot = session_read.read(session_key)
    if snapshot is None:
        return _unavailable_memory_status_projection()
    return _build_memory_status_projection(
        snapshot.messages,
        snapshot.last_consolidated,
    )


def _mobile_memory_status_query(
    session_read: SessionReadService,
    method: str,
    payload: dict[str, object],
    *,
    session_id: str | None,
    turn_id: str | None,
) -> dict[str, object]:
    """校验移动查询并返回既有 Session 的脱离投影。"""

    # 1. 在插件 RPC 边界限定唯一的只读任务
    _ = payload, turn_id
    if method != "memory.status":
        raise MobileUiRpcInvalidRequest(f"未知 status_commands 移动方法: {method}")
    if session_id is None or not session_id.strip():
        raise MobileUiRpcInvalidRequest("memory.status 缺少 session_id")

    # 2. 只读既有 Session，不触发创建或取得持久化 owner
    return dict(_read_memory_status(session_read, session_id))


def _build_memory_status_projection(
    messages: Sequence[Mapping[str, object]],
    last_consolidated: int,
) -> MemoryStatusProjection:
    """把会话整理游标投影为命令与移动端共用的稳定状态。"""

    # 1. 把持久化游标限制到当前会话窗口。
    last = max(0, min(int(last_consolidated), len(messages)))
    consolidated_user = _count_real_user_messages(messages[:last])
    total_user = _count_real_user_messages(messages)
    pending_user = max(0, total_user - consolidated_user)
    last_user_message = _latest_real_user_content(messages[:last])

    # 2. 状态摘要只描述用户现在需要知道的整理进度。
    if last <= 0 or not last_user_message:
        state: Literal["never", "pending", "up_to_date"] = "never"
        summary = "还没有完成过整理"
    elif pending_user == 0:
        state = "up_to_date"
        summary = "已整理到最新"
    else:
        state = "pending"
        summary = f"有 {pending_user} 条消息待整理"
    return {
        "state": state,
        "summary": summary,
        "pending_user_messages": pending_user,
        "message_count": len(messages),
        "last_consolidated_preview": (
            _preview_text(last_user_message) if last_user_message else None
        ),
    }


def _unavailable_memory_status_projection() -> MemoryStatusProjection:
    return {
        "state": "unavailable",
        "summary": "电脑端已不存在",
        "pending_user_messages": 0,
        "message_count": 0,
        "last_consolidated_preview": None,
    }


def _format_memory_status_reply(projection: MemoryStatusProjection) -> str:
    """把结构化记忆状态渲染为命令回复。"""

    lines = ["🧠 记忆整理状态："]
    if projection["state"] == "unavailable":
        lines.append("当前会话不存在，无法读取记忆整理状态。")
    elif projection["state"] == "never":
        lines.append("当前会话还没有完成过记忆整理。")
    elif projection["state"] == "up_to_date":
        lines.append("当前会话已经整理到最新的用户消息。")
    else:
        lines.append(
            f"上次整理到 {projection['pending_user_messages']} 条用户消息之前。"
        )
    preview = projection["last_consolidated_preview"]
    if preview:
        lines.extend(["", "最后已整理的用户消息：", f"“{preview}”"])
    lines.extend(
        [
            "",
            f"尚未整理的用户消息数：{projection['pending_user_messages']}",
            f"当前会话消息数：{projection['message_count']}",
        ]
    )
    return "\n".join(lines)


def _count_real_user_messages(messages: Sequence[Mapping[str, object]]) -> int:
    return sum(1 for item in messages if _is_real_user_message(item))


def _latest_real_user_content(messages: Sequence[Mapping[str, object]]) -> str:
    for item in reversed(messages):
        if _is_real_user_message(item):
            return _content_to_text(item.get("content", ""))
    return ""


def _is_real_user_message(item: Mapping[str, object]) -> bool:
    if item.get("role") != "user":
        return False
    content = _content_to_text(item.get("content", ""))
    return bool(content) and not is_context_frame(content)


def _content_to_text(content: object) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        raw_items = cast(list[object], content)
        parts: list[str] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            mapping = cast(dict[object, object], item)
            if mapping.get("type") == "text":
                parts.append(str(mapping.get("text", "")).strip())
        return "\n".join(part for part in parts if part).strip()
    return str(content).strip()


def _preview_text(text: str, limit: int = 80) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1] + "…"
