// plugins/status_commands/dashboard_panel.tsx
import { useEffect, useState } from "react";
import { Pie, api } from "@akashic/dashboard-ui";
import { jsx, jsxs } from "react/jsx-runtime";
function _formatNumber(value) {
  return new Intl.NumberFormat("zh-CN").format(Number(value || 0));
}
function _formatRate(value) {
  if (typeof value !== "number") {
    return "-";
  }
  return `${(value * 100).toFixed(1)}%`;
}
function _hitTone(rate) {
  if (rate == null) return "text-muted";
  if (rate >= 0.8) return "text-success";
  if (rate >= 0.5) return "text-warning";
  return "text-danger";
}
function _shortTs(value) {
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) {
    return value || "-";
  }
  return `${d.getMonth() + 1}-${String(d.getDate()).padStart(2, "0")} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}
var TABLE_GRID = "128px 92px 60px 92px 92px 1fr";
function KvMain(_props) {
  const [overview, setOverview] = useState(null);
  const [turns, setTurns] = useState([]);
  useEffect(() => {
    let alive = true;
    void (async () => {
      const [ov, page] = await Promise.all([
        api("/api/dashboard/status-commands/kvcache/overview"),
        api("/api/dashboard/status-commands/kvcache/turns?page=1&page_size=50")
      ]);
      if (alive) {
        setOverview(ov);
        setTurns(page.items ?? []);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);
  if (!overview) {
    return /* @__PURE__ */ jsx("div", { className: "p-5 text-[13px] text-muted", children: "\u52A0\u8F7D\u4E2D\u2026" });
  }
  const passive = overview.passive ?? overview;
  const recentPassive = turns.filter((turn) => turn.source === "agent").slice(0, 10);
  const recentPassiveHit = recentPassive.reduce((sum, turn) => sum + (turn.hit_tokens || 0), 0);
  const recentPassiveMiss = recentPassive.reduce((sum, turn) => sum + (turn.miss_tokens || 0), 0);
  const recentPassiveRate = recentPassiveHit + recentPassiveMiss > 0 ? recentPassiveHit / (recentPassiveHit + recentPassiveMiss) : null;
  const proactive = overview.proactive ?? {
    tracked_turn_count: 0,
    prompt_tokens: 0,
    hit_tokens: 0,
    miss_tokens: 0,
    hit_rate: null
  };
  return /* @__PURE__ */ jsxs("div", { className: "p-5", children: [
    /* @__PURE__ */ jsx("div", { className: "detail-title", children: "KV Cache" }),
    /* @__PURE__ */ jsx("div", { className: "detail-subtext", children: "\u6700\u8FD1\u51E0\u6B21 KVCache \u8C03\u7528 \xB7 token \u590D\u7528" }),
    /* @__PURE__ */ jsxs("div", { className: "mt-5 grid gap-3", style: { gridTemplateColumns: "repeat(3, minmax(0, 1fr))" }, children: [
      /* @__PURE__ */ jsx("div", { className: "min-w-0 overflow-hidden rounded-lg border border-border bg-surface p-3 shadow-lift-sm animate-fade-up", children: /* @__PURE__ */ jsx(Pie, { title: `\u6700\u8FD1 10 \u6B21\u88AB\u52A8\u94FE\u8DEF \xB7 ${recentPassive.length} \u8F6E`, rate: recentPassiveRate, hit: recentPassiveHit, miss: recentPassiveMiss }) }),
      /* @__PURE__ */ jsx("div", { className: "min-w-0 overflow-hidden rounded-lg border border-border bg-surface p-3 shadow-lift-sm animate-fade-up", style: { animationDelay: "80ms" }, children: /* @__PURE__ */ jsx(Pie, { title: `\u5168\u5C40\u88AB\u52A8\u94FE\u8DEF \xB7 ${passive.tracked_turn_count} \u8F6E`, rate: passive.hit_rate, hit: passive.hit_tokens, miss: passive.miss_tokens }) }),
      /* @__PURE__ */ jsx("div", { className: "min-w-0 overflow-hidden rounded-lg border border-border bg-surface p-3 shadow-lift-sm animate-fade-up", style: { animationDelay: "160ms" }, children: /* @__PURE__ */ jsx(Pie, { title: `\u5168\u5C40\u4E3B\u52A8\u94FE\u8DEF \xB7 ${proactive.tracked_turn_count} \u8F6E`, rate: proactive.hit_rate, hit: proactive.hit_tokens, miss: proactive.miss_tokens }) })
    ] }),
    /* @__PURE__ */ jsxs("div", { className: "animate-fade-up mt-5 overflow-hidden rounded-lg border border-border", style: { animationDelay: "220ms" }, children: [
      /* @__PURE__ */ jsxs(
        "div",
        {
          className: "grid items-center border-b border-border-strong bg-surface-2 px-3 py-2 font-mono text-[10px] uppercase tracking-[0.14em] text-subtle",
          style: { gridTemplateColumns: TABLE_GRID, columnGap: "10px" },
          children: [
            /* @__PURE__ */ jsx("div", { children: "Session" }),
            /* @__PURE__ */ jsx("div", { children: "Time" }),
            /* @__PURE__ */ jsx("div", { className: "text-right", children: "Hit" }),
            /* @__PURE__ */ jsx("div", { className: "text-right", children: "Hit Tok" }),
            /* @__PURE__ */ jsx("div", { className: "text-right", children: "Prompt" }),
            /* @__PURE__ */ jsx("div", { children: "User" })
          ]
        }
      ),
      /* @__PURE__ */ jsx("div", { className: "max-h-[42vh] overflow-auto", children: turns.length === 0 ? /* @__PURE__ */ jsx("div", { className: "px-3 py-4 text-[12.5px] text-muted", children: "\u6682\u65E0 KVCache \u8BB0\u5F55\u3002" }) : turns.map((t) => /* @__PURE__ */ jsxs(
        "div",
        {
          className: "grid items-center border-b border-border px-3 py-2 text-[12.5px] last:border-b-0 hover:bg-surface-2",
          style: { gridTemplateColumns: TABLE_GRID, columnGap: "10px" },
          children: [
            /* @__PURE__ */ jsx("div", { className: "truncate font-mono tabular-nums text-muted", title: t.session_key, children: t.session_key }),
            /* @__PURE__ */ jsx("div", { className: "font-mono tabular-nums text-muted", children: _shortTs(t.ts) }),
            /* @__PURE__ */ jsx("div", { className: `text-right font-mono tabular-nums ${_hitTone(t.hit_rate)}`, children: _formatRate(t.hit_rate) }),
            /* @__PURE__ */ jsx("div", { className: "text-right font-mono tabular-nums text-fg", children: _formatNumber(t.hit_tokens) }),
            /* @__PURE__ */ jsx("div", { className: "text-right font-mono tabular-nums text-muted", children: _formatNumber(t.prompt_tokens) }),
            /* @__PURE__ */ jsx("div", { className: "truncate text-fg", children: t.user_preview || "\uFF08\u65E0\u5185\u5BB9\uFF09" })
          ]
        },
        t.id
      )) })
    ] })
  ] });
}
window.AkashicDashboard.registerPlugin({
  id: "status_commands",
  label: "KV Cache",
  viewLabel: "kv cache",
  layout: "workbench",
  pageSize: 25,
  rowKey: "id",
  countTitle(total) {
    return `${total} \u8F6E KVCache`;
  },
  columns: [
    { key: "session_key", label: "Session", width: 108, fmt: "mono-session", cellClass: "mono cell-session", rawTitle: true },
    { key: "ts", label: "Time", width: 96, fmt: "mono-time", cellClass: "mono cell-time", rawTitle: true },
    { key: "hit_rate", label: "Hit", width: 72, fmt: "cache-rate", cellClass: "mono cell-metric", align: "right" },
    { key: "user_preview", label: "User", flex: true, fmt: "text-preview", cellClass: "content-preview" }
  ],
  async getCount() {
    try {
      const summary = await api("/api/dashboard/status-commands/kvcache/overview");
      return summary.tracked_turn_count || 0;
    } catch {
      return null;
    }
  },
  async fetchPage({ page, pageSize }) {
    const params = new URLSearchParams();
    params.set("page", String(page));
    params.set("page_size", String(pageSize));
    const data = await api(
      `/api/dashboard/status-commands/kvcache/turns?${params.toString()}`
    );
    return { items: data.items || [], total: data.total || 0 };
  },
  Main: KvMain,
  formatters: {
    "cache-rate": (value) => _formatRate(value),
    number: (value) => _formatNumber(value)
  }
});
