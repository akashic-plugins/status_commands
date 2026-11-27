from __future__ import annotations


import pytest
from plugins.compaction.records import SummaryLookup, SummaryRecord, SummaryRecords
from plugins.content.plugin import check_text
from plugins.ledger.log import MessageCatalog, MessageLog
from plugins.ledger.contract import ContentPart, ContentReferences, Input, Output

import status_commands_source.plugin as plugin_module


@pytest.fixture
def state(tmp_path):
    (tmp_path / "workspace").mkdir()
    log = MessageLog(tmp_path / 'workspace' / 'sessions.db')
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
    ), log.reader('s'), parent=None)


@pytest.mark.asyncio
async def test_projection_reads_actual_summary_coverage_and_keeps_literal_user_text(state):
    log, records, lookup = state
    before = log.reader('s').snapshot()
    never = plugin_module._read_memory_status(MessageCatalog(log), lookup, 's')
    assert never['state'] == 'never' and never['pending_user_messages'] == 2
    await publish(log, records)
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
    result = plugin_module._plugin_ui_memory_status_query(catalog, lookup, 'memory.status', {}, session_id='missing', turn_id=None)
    assert result['state'] == 'unavailable'
    assert dict(catalog.snapshot_heads()) == before
    with pytest.raises(plugin_module.PluginUiRpcInvalidRequest, match='缺少 session_id'):
        plugin_module._plugin_ui_memory_status_query(catalog, lookup, 'memory.status', {}, session_id=None, turn_id=None)
    with pytest.raises(plugin_module.PluginUiRpcInvalidRequest, match='未知'):
        plugin_module._plugin_ui_memory_status_query(catalog, lookup, 'write', {}, session_id='s', turn_id=None)


@pytest.mark.asyncio
async def test_legacy_user_role_counts_without_inventing_author(state):
    log, records, lookup = state
    await publish(log, records)
    writer = log.writer('s', author='legacy-attribution-unknown', source='legacy-unattributed',
                        body_types=(Input,), content={'text': check_text, 'history.provenance': lambda part: ContentReferences()})
    old = writer.append('legacy-user', Input((ContentPart('text', '旧用户正文'), ContentPart('history.provenance', {
        'schema': 'sessions.messages.v0', 'role': 'user', 'content_was_null': False,
        'extra': None, 'extra_sha256': None,
    }))))
    result = plugin_module._read_memory_status(MessageCatalog(log), lookup, 's')
    assert result['pending_user_messages'] == 2
    assert log.reader('s').get(old.message_id).author == 'legacy-attribution-unknown'
