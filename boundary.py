"""Status commands 与 compaction/message Core 之间的中立 v3 边界。"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from agent.plugin_composition import ServiceKey
from agent.plugin_composition.messages import MessageCatalog
from agent.plugin_contracts import Message


class SummaryView(Protocol):
    @property
    def source_message_ids(self) -> tuple[str, ...]: ...


class SummaryLookup(Protocol):
    def head(self, session_id: str) -> SummaryView | None: ...


COMPACTION_SUMMARIES = ServiceKey[SummaryLookup]("compaction.summaries.v1")


__all__ = [
    "COMPACTION_SUMMARIES",
    "Message",
    "MessageCatalog",
    "SummaryLookup",
    "SummaryView",
]
