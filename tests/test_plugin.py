from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from agent.plugins.manager import PluginManager
from agent.plugin_composition import COMMANDS
from agent.plugins.mobile_ui import PluginMobileUiProvider
from agent.plugins.snapshot import lease_runtime_snapshot
from bus.event_bus import EventBus
from agent.plugins.selection import PluginSelection
from plugins.compaction.records import SummaryLookup, SummaryRecord, SummaryRecords
from plugins.context.api import summary_range
from plugins.content.plugin import check_text
from session.log import MessageCatalog, MessageLog
from session.message import ContentPart, ContentReferences, Input, Output

import status_commands_source.plugin as plugin_module


@pytest.fixture
def state(tmp_path):
    log = MessageLog(tmp_path / 'sessions.db')
    inputs = log.writer('s', author='user', source='conversation', body_types=(Input,), content={'text': check_text})
    outputs = log.writer('s', author='assistant', source='conversation', body_types=(Output,), content={'text': check_text})
    inputs.append('u1', Input((ContentPart('text', '已整理的问题'),)))
    outputs.append('a1', Output((ContentPart('text', '旧回答'),), 'complete'))
    inputs.append('u2', Input((ContentPart('text', '待整理的问题'),)))
    records = SummaryRecords(log.owner('plugin:compaction'))
    lookup = SummaryLookup(records.read, records.head)
    yield log, records, lookup
    log.close()


def publish(log, records):
    return records.publish(SummaryRecord(
        reference='summary-1', session_id='s', generation=1, parent=None,
        source_message_ids=('u1', 'a1'), content='已整理',
        summary_message_ids=('u1', 'a1'), omitted_message_ids=(),
        model_call_ids=('fixture-call',),
        trigger='soft_limit', context_window=1000, max_output_tokens=100,
        keep_recent_tokens=100, tokens_before=800, tokens_after=300,
    ), log.reader('s'), parent=None, summary_range=summary_range)


def test_projection_reads_actual_summary_coverage_and_keeps_literal_user_text(state):
    log, records, lookup = state
    before = log.reader('s').snapshot()
    never = plugin_module._read_memory_status(MessageCatalog(log), lookup, 's')
    assert never['state'] == 'never' and never['pending_user_messages'] == 2
    publish(log, records)
    result = plugin_module._read_memory_status(MessageCatalog(log), lookup, 's')
    assert result == {'state': 'pending', 'summary': '有 1 条消息待整理',
                      'pending_user_messages': 1, 'message_count': 3,
                      'last_consolidated_preview': '已整理的问题'}
    assert log.reader('s').snapshot() == before
    writer = log.writer('s', author='user', source='conversation', body_types=(Input,), content={'text': check_text})
    writer.append('literal', Input((ContentPart('text', '[Context Frame]\n这是用户写的示例'),)))
    assert plugin_module._read_memory_status(MessageCatalog(log), lookup, 's')['pending_user_messages'] == 2


def test_unknown_session_is_not_created_and_rpc_rejects_unknown_requests(state):
    log, _records, lookup = state
    catalog = MessageCatalog(log)
    before = dict(catalog.snapshot_heads())
    result = plugin_module._mobile_memory_status_query(catalog, lookup, 'memory.status', {}, session_id='missing', turn_id=None)
    assert result['state'] == 'unavailable'
    assert dict(catalog.snapshot_heads()) == before
    with pytest.raises(plugin_module.MobileUiRpcInvalidRequest, match='缺少 session_id'):
        plugin_module._mobile_memory_status_query(catalog, lookup, 'memory.status', {}, session_id=None, turn_id=None)
    with pytest.raises(plugin_module.MobileUiRpcInvalidRequest, match='未知'):
        plugin_module._mobile_memory_status_query(catalog, lookup, 'write', {}, session_id='s', turn_id=None)


@pytest.mark.asyncio
async def test_real_manager_command_and_mobile_read_same_persisted_summary(tmp_path, state):
    log, records, _lookup = state
    publish(log, records)
    source = tmp_path / 'plugins'
    plugin_root = source / 'status_commands'
    shutil.copytree(Path(plugin_module.__file__).parent, plugin_root,
                    ignore=shutil.ignore_patterns('.git', '.venv', '__pycache__', '.pytest_cache'))
    provider = source / 'compaction'
    provider.mkdir()
    (provider / 'plugin.py').write_text('''from agent.plugin_composition import RUNTIME_STARTED
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
    await ctx.on(RUNTIME_STARTED, start)
    await ctx.provide(COMPACTION_SUMMARIES, SummaryLookup(
        lambda ref: records.read(ref), lambda session: records.head(session)))
''')
    for provider_name in ('commands', 'ui'):
        shutil.copytree(
            Path(__import__(f'plugins.{provider_name}.plugin', fromlist=['x']).__file__).parents[0],
            source / provider_name,
        )
    (tmp_path / 'workspace').mkdir()
    PluginSelection(tmp_path / 'workspace').initialize()
    manager = PluginManager([source], event_bus=EventBus(), workspace=tmp_path / 'workspace',
                            message_log=log, installed_cache_root=tmp_path / 'cache')
    async def _switch(old, new):
        _ = (old, new)
    manager.bind_endpoint_switcher(_switch)
    try:
        await manager.load_all()
        await manager.start_runtime()
        before = log.reader('s').snapshot()
        async with lease_runtime_snapshot(manager.snapshot_store) as snapshot:
            assert snapshot.composition_root is not None
            commands = snapshot.composition_root.context.require(COMMANDS).freeze()
            execution = await commands.execute('/memory_status', session_key='s', channel='web', chat_id='s', sender='hua')
            assert execution.result.kind == 'success'
            assert '尚未整理的用户消息数：1' in execution.result.text
            provider = PluginMobileUiProvider(manager.snapshot_store)
            item = next(row for row in provider.catalog()['items'] if row['id'] == 'status_commands')
            result = await provider.query('status_commands', item['revision'], 'memory.status', {}, session_id='s', turn_id=None)
            assert result['last_consolidated_preview'] == '已整理的问题'
            assert result['pending_user_messages'] == 1
            assert log.reader('s').snapshot() == before
    finally:
        await manager.terminate_all()


def test_legacy_user_role_counts_without_inventing_author(state):
    log, records, lookup = state
    publish(log, records)
    writer = log.writer('s', author='legacy-attribution-unknown', source='legacy-unattributed',
                        body_types=(Input,), content={'text': check_text, 'history.provenance': lambda part: ContentReferences()})
    old = writer.append('legacy-user', Input((ContentPart('text', '旧用户正文'), ContentPart('history.provenance', {
        'schema': 'sessions.messages.v0', 'role': 'user', 'content_was_null': False,
        'extra': None, 'extra_sha256': None,
    }))))
    result = plugin_module._read_memory_status(MessageCatalog(log), lookup, 's')
    assert result['pending_user_messages'] == 2
    assert log.reader('s').get(old.message_id).author == 'legacy-attribution-unknown'
