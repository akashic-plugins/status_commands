# status_commands 插件

通过 `/memorystatus`、`/memory_status`、`/compact_status` 和移动会话面板查看记忆整理状态。

插件只读取 `core.message_catalog` 中已有 Message，以及 Compaction 的 `compaction.summaries.v1` 当前已发布摘要。摘要的 `source_message_ids` 决定已整理范围；generation 和 seq 都不作为数组下标。只统计 author 为 user 的 Input，用户正文里的协议示例仍是正常输入。

命令和面板共用一份投影：待整理用户消息数、当前消息总数、最后已整理用户消息预览。未知会话返回 unavailable，不创建 Session。没有已发布摘要时返回 never。查询没有消息、摘要写入或模型调用权限。

需要重构后的 Message Core 与 Compaction 插件。KV Cache、usage 和工具遥测由 Observe 拥有。

```bash
AKASHIC_AGENT_ROOT=/path/to/core PYTHONPATH=/path/to/core pytest -q
node --test tests/test_mobile_panel.mjs
PYTHONPATH=/path/to/core pyright --level error plugin.py
```

测试包括真实 MessageLog / SummaryRecords 的读取，以及真实 PluginManager 发布的命令和 Mobile 查询；测试中的摘要 provider 只提供同一持久 SummaryRecords 的窄读取口。
