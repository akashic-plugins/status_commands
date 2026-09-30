"""用真实插件命令核对慢读取、取消和卸载；只操作临时消息库。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

from agent.plugin_composition import COMMANDS
from agent.plugins.manager import PluginManager
from agent.plugins.selection import PluginSelection
from bus.event_bus import EventBus
from plugins.compaction.records import SummaryRecord, SummaryRecords
from plugins.content.plugin import check_text
from plugins.context.api import summary_range
from session.log import MessageLog, MessageReader
from session.message import ContentPart, Input, Message, Output


def seed(log: MessageLog) -> None:
    """保存真实消息与已发布摘要，再追加足以覆盖全历史读取的正文。"""
    # 1. 摘要覆盖身份保持固定，后来的用户消息仍待整理。
    inputs = log.writer("s", author="user", source="conversation",
                        body_types=(Input,), content={"text": check_text})
    outputs = log.writer("s", author="assistant", source="conversation",
                         body_types=(Output,), content={"text": check_text})
    inputs.append("u1", Input((ContentPart("text", "已整理的问题"),)))
    outputs.append("a1", Output((ContentPart("text", "旧回答"),), "complete"))
    SummaryRecords(log.owner("plugin:compaction")).publish(SummaryRecord(
        reference="summary-1", session_id="s", generation=1, parent=None,
        source_message_ids=("u1", "a1"), content="已整理",
        summary_message_ids=("u1", "a1"), omitted_message_ids=(),
        model_call_ids=("fixture-call",), trigger="soft_limit",
        context_window=1000, max_output_tokens=100, keep_recent_tokens=100,
        tokens_before=800, tokens_after=300,
    ), log.reader("s"), parent=None, summary_range=summary_range)
    for index in range(600):
        inputs.append(f"u-{index}", Input((ContentPart("text", "长历史 " * 1000),)))


def rows_digest(path: Path) -> str:
    """从实际 SQLite 行计算只读调用前后的事实身份。"""
    with sqlite3.connect(path) as connection:
        rows = {
            table: connection.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
            for table in ("sessions", "messages", "owner_records")
        }
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode()).hexdigest()


async def load_manager(source: Path, root: Path, log: MessageLog) -> PluginManager:
    """通过真实安装与命令注册链加载插件，摘要夹具只提供实际持久读取口。"""
    # 1. 候选代码从独立源码目录复制；不读取或修改安装 cache。
    plugins = root / "plugins"
    target = plugins / "status_commands"
    target.mkdir(parents=True)
    for name in ("plugin.py", "boundary.py", "plugin_ui.js", "plugin_ui.css"):
        shutil.copy2(source / name, target / name)
    provider = plugins / "compaction"
    provider.mkdir()
    (provider / "plugin.py").write_text('''from agent.plugin_composition import RUNTIME_STARTED, RUNTIME_STOPPING
from agent.plugin_composition.messages import OWNER_STATE
from plugins.compaction.records import COMPACTION_SUMMARIES, SummaryLookup, SummaryRecords
api_version = 3
name = "compaction"
version = "1.0.0"
inject = (OWNER_STATE,)
async def apply(ctx):
    records = None
    async def start(event):
        nonlocal records
        records = SummaryRecords(ctx.require(OWNER_STATE).open(ctx))
    async def stop(event):
        nonlocal records
        records = None
    await ctx.on(RUNTIME_STARTED, start)
    await ctx.on(RUNTIME_STOPPING, stop)
    await ctx.provide(COMPACTION_SUMMARIES, SummaryLookup(
        lambda ref: records.read(ref), lambda session: records.head(session)))
''')
    for name in ("commands", "ui"):
        module = __import__(f"plugins.{name}.plugin", fromlist=["__file__"])
        shutil.copytree(Path(module.__file__).parent, plugins / name,
                        ignore=shutil.ignore_patterns("__pycache__"))
    workspace = root / "workspace"
    workspace.mkdir()
    PluginSelection(workspace).initialize()
    manager = PluginManager([plugins], event_bus=EventBus(), workspace=workspace,
                            message_log=log, installed_cache_root=root / "cache")

    async def switch(old, new):
        _ = old, new

    manager.bind_endpoint_switcher(switch)
    await manager.load_all()
    await manager.start_runtime()
    return manager


async def scenario(source: Path, kind: str, expect_blocking: bool) -> dict[str, object]:
    """在原快照入口暂停物理工作，独立线程保证失败场景也会释放。"""
    with TemporaryDirectory(prefix="status-command-io-") as directory:
        root = Path(directory)
        database = root / "sessions.db"
        log = MessageLog(database)
        seed(log)
        before = rows_digest(database)
        manager = await load_manager(source, root, log)
        live = manager.live_root
        assert live is not None
        commands = live.context.require(COMMANDS).freeze()
        loop = asyncio.get_running_loop()
        entered = threading.Event()
        checkpoint = threading.Event()
        release = threading.Event()
        physical_done = threading.Event()
        observed: dict[str, object] = {}
        original = MessageReader.snapshot
        caller = None
        stopping = None

        def paused(
            self: MessageReader, *, after_seq: int = -1, through_seq: int | None = None,
        ) -> tuple[Message, ...]:
            observed["physical_entered_at"] = time.monotonic()
            entered.set()
            observed["worker_thread"] = threading.get_ident()
            if not release.wait(10):
                raise TimeoutError("physical read barrier was not released")
            try:
                return original(self, after_seq=after_seq, through_seq=through_seq)
            finally:
                observed["physical_done_at"] = time.monotonic()
                physical_done.set()

        def loop_checkpoint() -> None:
            nonlocal stopping
            observed["loop_checkpoint_at"] = time.monotonic()
            assert caller is not None
            observed["pending_before_release"] = not caller.done()
            if kind == "cancel":
                caller.cancel()
                loop.call_soon(caller.cancel)
            elif kind == "terminate":
                stopping = asyncio.create_task(manager.terminate_all())
            loop.call_soon(check_draining)

        def check_draining() -> None:
            assert caller is not None
            observed["caller_draining"] = not caller.done()
            observed["termination_draining"] = stopping is None or not stopping.done()
            checkpoint.set()

        def controller() -> None:
            if not entered.wait(10):
                observed["controller_error"] = "command did not reach actual snapshot"
                release.set()
                return
            loop.call_soon_threadsafe(loop_checkpoint)
            observed["checkpoint_before_release"] = checkpoint.wait(1)
            observed["physical_released_at"] = time.monotonic()
            release.set()

        # 2. 只有入口屏障替换；释放后读取原数据库、构造原命令结果。
        thread = threading.Thread(target=controller, daemon=True)
        MessageReader.snapshot = paused
        started = time.monotonic()
        try:
            thread.start()
            caller = asyncio.create_task(commands.execute(
                "/memory_status", session_key="s", channel="web", chat_id="s", sender="fixture",
            ))
            try:
                result = await asyncio.wait_for(asyncio.shield(caller), 15)
            except asyncio.CancelledError:
                assert kind == "cancel"
                observed["cancelled"] = True
            else:
                assert kind != "cancel" or expect_blocking
                assert result is not None and result.result.kind == "success"
                assert "尚未整理的用户消息数：600" in result.result.text
                observed["command_result"] = result.result.text
            assert physical_done.is_set()
            if stopping is not None:
                await asyncio.wait_for(stopping, 15)
            thread.join(2)
            assert not thread.is_alive()
            assert "controller_error" not in observed
            assert observed["checkpoint_before_release"] is not expect_blocking
            if not expect_blocking:
                assert observed["worker_thread"] != threading.get_ident()
                assert observed["pending_before_release"] is True
                assert observed["caller_draining"] is True
                assert observed["termination_draining"] is True
            observed["elapsed_seconds"] = time.monotonic() - started
            observed["checkpoint_lag_seconds"] = (
                cast(float, observed["loop_checkpoint_at"])
                - cast(float, observed["physical_entered_at"])
            )
        finally:
            release.set()
            MessageReader.snapshot = original
            thread.join(2)
            if caller is not None and not caller.done():
                await asyncio.gather(caller, return_exceptions=True)
            await manager.terminate_all()
            assert rows_digest(database) == before
            log.close()
        return {"kind": kind, "source": str(source), "rows_unchanged": True, **observed}


async def main() -> None:
    """保存基线或候选的实际命令证据，任何未满足条件都返回非零。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--expect-blocking", action="store_true")
    args = parser.parse_args()
    kinds = ("normal",) if args.expect_blocking else ("normal", "cancel", "terminate")
    receipts = [await scenario(args.source.resolve(), kind, args.expect_blocking) for kind in kinds]
    print(json.dumps(receipts, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
