# status_commands 插件

记忆整理状态插件。API v3 入口通过 `core.commands` 声明 `/memorystatus`，通过 `core.session_read` 读取脱离的既有 Session 快照，并通过 `core.ui_slots` 发布 Android 会话抽屉面板。

本插件不注册 Dashboard，也不再声明 `/kvcache`。KV Cache 的采集、查询、看板与 Turn 尾部统计都由数据所有者 `observe` 插件负责。

---

## 接入点

| 接入方式 | 阶段 |
|---|---|
| `core.commands` | 命令识别后直接返回插件结果，不创建 Session、不进入 LLM |
| `core.session_read` | 读取既有 Session 的脱离快照，不取得持久化 owner |
| `core.ui_slots` | `drawer.panel`——当前既有会话的只读记忆整理状态 |

---

## 运作逻辑

插件 Fiber 在 `apply(ctx, config)` 中登记命令与 Mobile UI Effect。Core 冻结 generation 后才发布命令目录、执行 handler 和查询界面；候选失败或 generation 退役时，Root 负责精确清理登记。

### 记忆状态（`/memorystatus` / `/memory_status` / `/compact_status`）

读取当前 session 的 `messages` 列表和 `last_consolidated` 指针，统计：

- 已整理到的用户消息数量（`last_consolidated` 之前）。
- 尚未整理的用户消息数量。
- 最后一条已整理用户消息的预览。
- 当前会话总消息数。

格式化为可读文本后作为 abort_reply 返回。只统计"真实用户消息"（role=user 且非 context frame 占位符）。

同一份结构化 projection 也用于 Android 抽屉面板。命令和移动查询都只通过 `core.session_read` 读取快照；会话不存在时返回中性的 `unavailable` 投影，不会因为状态查询而重新创建。面板默认折叠，只显示摘要和待整理数；每次展开都会重新读取同一会话，展开后显示最新的消息计数和最后已整理预览。

测试需要把 Agent 主仓加入导入路径：

```bash
PYTHONPATH=/path/to/akasic-agent AKASHIC_AGENT_ROOT=/path/to/akasic-agent pytest -q
node --test tests/test_mobile_panel.mjs
PYTHONPATH=/path/to/akasic-agent pyright plugin.py
```
