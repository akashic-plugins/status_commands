from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI

from .kvcache_reader import KVCacheDashboardReader


def register(app: FastAPI, plugin_dir: Path, workspace: Path) -> None:
    reader = KVCacheDashboardReader(workspace)

    @app.get("/api/dashboard/status-commands/kvcache/overview")
    def get_kvcache_overview() -> dict[str, Any]:
        return reader.get_summary()

    @app.get("/api/dashboard/status-commands/kvcache/turns")
    def list_kvcache_turns(
        page: int = 1,
        page_size: int = 25,
    ) -> dict[str, Any]:
        items, total = reader.list_turns(page=page, page_size=page_size)
        return {
            "items": items,
            "total": total,
            "page": max(1, page),
            "page_size": max(1, min(page_size, 100)),
        }

    @app.get("/api/dashboard/status-commands/kvcache/turns/{turn_id}")
    def get_kvcache_turn(turn_id: int) -> dict[str, Any]:
        item = reader.get_turn(turn_id)
        if item is None:
            return {}
        return {**item, "summary": reader.get_summary()}
