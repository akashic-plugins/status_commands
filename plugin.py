from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Literal, TypedDict

from agent.plugin_composition import (
    COMMANDS,
    UI_SLOTS,
    CommandDefinition,
    CommandInvocation,
    CommandResult,
    Context,
    MobileUiDefinition,
    MobileUiRpcInvalidRequest,
)
from agent.plugin_composition.messages import MESSAGE_CATALOG
from agent.plugin_composition.messages import MessageCatalog
from agent.plugin_contracts import ContentPart, Input, Message

if __package__:
    from .boundary import COMPACTION_SUMMARIES, SummaryLookup
else:  # test harness imports the entrypoint as a standalone module
    from boundary import COMPACTION_SUMMARIES, SummaryLookup

logger = logging.getLogger("plugin.status_commands")

api_version = 3
name = "status_commands"
version = "3.0.0"
inject = (COMMANDS, MESSAGE_CATALOG, COMPACTION_SUMMARIES, UI_SLOTS)


class MemoryStatusProjection(TypedDict):
    state: Literal["never", "pending", "up_to_date", "unavailable"]
    summary: str
    pending_user_messages: int
    message_count: int
    last_consolidated_preview: str | None


async def apply(ctx: Context) -> None:
    """登记记忆状态命令和移动端只读界面。"""

    # 1. 只取得命令和界面共同依赖的消息与摘要读取口
    catalog = ctx.require(MESSAGE_CATALOG)
    summaries = ctx.require(COMPACTION_SUMMARIES)

    async def handle_memory_status(
        invocation: CommandInvocation,
    ) -> CommandResult:
        projection = _read_memory_status(catalog, summaries, invocation.session_key)
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
            catalog, summaries,
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
    catalog: MessageCatalog, summaries: SummaryLookup, session_key: str,
) -> MemoryStatusProjection:
    """只读已有消息和 Compaction 当前摘要，不创建会话。"""
    reader = catalog.reader(session_key)
    if session_key not in catalog.snapshot_heads():
        return _unavailable_memory_status_projection()
    snapshot = reader.snapshot()
    summary = summaries.head(session_key)
    return _build_memory_status_projection(
        snapshot, None if summary is None else frozenset(summary.source_message_ids),
    )


def _mobile_memory_status_query(
    catalog: MessageCatalog, summaries: SummaryLookup,
    method: str, payload: dict[str, object], *,
    session_id: str | None, turn_id: str | None,
) -> dict[str, object]:
    """在 RPC 边界限定只读任务，命令和面板消费同一摘要覆盖集合。"""
    _ = payload, turn_id
    if method != "memory.status":
        raise MobileUiRpcInvalidRequest(f"未知 status_commands 移动方法: {method}")
    if session_id is None or not session_id.strip():
        raise MobileUiRpcInvalidRequest("memory.status 缺少 session_id")
    return dict(_read_memory_status(catalog, summaries, session_id))


def _build_memory_status_projection(
    messages: Sequence[Message], source_message_ids: frozenset[str] | None,
) -> MemoryStatusProjection:
    """按摘要真正覆盖的消息身份计数，不把 generation 或 seq 当数组下标。"""
    # 1. 用户身份来自 Message；用户写出的协议示例也是正常正文。
    user_messages = tuple(item for item in messages if _is_user_input(item))
    covered = source_message_ids or frozenset()
    consolidated = tuple(item for item in user_messages if item.message_id in covered)
    pending_user = len(user_messages) - len(consolidated)
    last_user_message = _text(consolidated[-1]) if consolidated else ""

    # 2. 当前 head 是已发布事实，未发布的模型请求不算整理完成。
    if source_message_ids is None:
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
        "last_consolidated_preview": _preview_text(last_user_message) if last_user_message else None,
    }


def _is_user_input(message: Message) -> bool:
    """旧消息保留未知作者，以原 role 事实计数，不改写用户身份。"""
    if not isinstance(message.body, Input):
        return False
    if message.author == "user":
        return True
    return message.source == "legacy-unattributed" and any(
        isinstance(part, ContentPart) and part.kind == "history.provenance"
        and isinstance(part.value, Mapping)
        and part.value.get("schema") == "sessions.messages.v0"
        and part.value.get("role") == "user"
        for part in message.body.parts
    )


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


def _text(message: Message) -> str:
    assert isinstance(message.body, Input)
    return "\n".join(str(part.value) for part in message.body.parts
                     if isinstance(part, ContentPart) and part.kind == "text").strip()


def _preview_text(text: str, limit: int = 80) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1] + "…"
