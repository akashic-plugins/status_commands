# status_commands 插件

内置诊断命令拦截器。在 BeforeTurn 管道的早期阶段识别 `/memory_status` 和 `/kvcache` 命令，直接返回诊断报告，绕过后续的记忆检索和 LLM 推理。

本插件不注册 Dashboard。它只在 Android 会话抽屉提供当前会话的记忆整理状态；KV Cache 的采集、看板与 Turn 尾部统计仍由数据所有者 `observe` 插件提供，`/kvcache` 仅保留为命令行式只读入口。

---

## 接入点

| 接入方式 | 阶段 |
|---|---|
| `before_turn_modules()` | `before_turn.acquire_session` 之后——命令识别与 abort |
| `mobile_ui_module()` | `drawer.panel`——当前既有会话的只读记忆整理状态 |

---

## 运作逻辑

两个命令各对应一个 PhaseModule，均插入在记忆检索（`_PrepareContextModule`）之前。任意一个命中时，向 `session:ctx` slot 写入一个 `abort=True` 的 `BeforeTurnCtx`，后续管道模块及 LLM 推理全部跳过，直接返回该 slot 的内容作为本轮回复。

### MemoryStatusCommandModule（`/memory_status` / `/compact_status`）

读取当前 session 的 `messages` 列表和 `last_consolidated` 指针，统计：

- 已整理到的用户消息数量（`last_consolidated` 之前）。
- 尚未整理的用户消息数量。
- 最后一条已整理用户消息的预览。
- 当前会话总消息数。

格式化为可读文本后作为 abort_reply 返回。只统计"真实用户消息"（role=user 且非 context frame 占位符）。

同一份结构化 projection 也用于 Android 抽屉面板。移动 RPC 通过 SessionManager 的只读加载路径读取既有会话，兼容尚未提供 `get_existing()` 的 runtime；失效会话返回中性的 `unavailable` 投影，不会因为状态查询而重新创建。面板默认折叠，只显示摘要和待整理数；每次展开都会重新读取同一会话，展开后显示最新的消息计数和最后已整理预览。

测试需要把 Agent 主仓加入导入路径：

```bash
PYTHONPATH=/path/to/akasic-agent AKASHIC_AGENT_ROOT=/path/to/akasic-agent pytest -q
node --test tests/test_mobile_panel.mjs
PYTHONPATH=/path/to/akasic-agent pyright plugin.py
```

### KVCacheCommandModule（`/kvcache` / `/cache_status`）

查询 observe 数据库（`observe/observe.db`），从 `turns` 表取最近 N 轮（默认 5，可追加参数覆盖，最大 30）的 KVCache 统计字段：

- `react_cache_prompt_tokens`：本轮送入的 prompt tokens 总量。
- `react_cache_hit_tokens`：命中缓存的 tokens 数量。

计算每轮命中率和总体命中率，格式化为表格后返回。若 observe 数据库不存在则返回提示信息。
