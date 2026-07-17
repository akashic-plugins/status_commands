import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = await readFile(new URL("../mobile_panel.js", import.meta.url), "utf8");
const panel = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);

class FakeElement {
  constructor() {
    this.attributes = new Map();
    this.children = [];
    this.className = "";
    this.dataset = {};
    this.hidden = false;
    this.inert = false;
    this.listeners = new Map();
    this.textContent = "";
    this.classList = {
      toggle: (name, enabled) => {
        const classes = new Set(this.className.split(" ").filter(Boolean));
        enabled ? classes.add(name) : classes.delete(name);
        this.className = Array.from(classes).join(" ");
        return enabled;
      },
    };
  }

  addEventListener(name, listener) { this.listeners.set(name, listener); }
  append(...children) { this.children.push(...children); }
  click() { this.listeners.get("click")?.(); }
  getAttribute(name) { return this.attributes.get(name); }
  querySelector(selector) { return this.queries[selector]; }
  setAttribute(name, value) { this.attributes.set(name, value); }
}

class PanelHost extends FakeElement {
  constructor() {
    super();
    this.trigger = new FakeElement();
    this.trigger.setAttribute("aria-expanded", "false");
    this.summary = new FakeElement();
    this.pending = new FakeElement();
    this.chevron = new FakeElement();
    this.trigger.queries = {
      strong: this.summary,
      ".status-memory-pending": this.pending,
      ".status-memory-chevron": this.chevron,
    };
    this.detail = new FakeElement();
    this.detail.hidden = true;
    this.detail.inert = true;
    this.pendingValue = new FakeElement();
    this.totalValue = new FakeElement();
    this.detail.queries = {
      '[data-value="pending"]': this.pendingValue,
      '[data-value="total"]': this.totalValue,
    };
    this.preview = new FakeElement();
    this.preview.hidden = true;
    this.previewText = new FakeElement();
    this.preview.queries = { p: this.previewText };
  }

  set innerHTML(value) { this.html = value; }

  querySelector(selector) {
    return {
      ".status-memory-trigger": this.trigger,
      ".status-memory-detail": this.detail,
      ".status-memory-preview": this.preview,
    }[selector];
  }
}

test("mobile surface is drawer-only and does not reclaim KV Cache", () => {
  assert.equal(typeof panel.default.slots["drawer.panel"].mount, "function");
  assert.equal(panel.default.dashboard, undefined);
  assert.equal(panel.default.navigation, undefined);
  assert.doesNotMatch(source, /kvcache/i);
});

test("drawer stays empty when there is no current session", () => {
  const host = new PanelHost();
  let requested = false;
  panel.default.slots["drawer.panel"].mount(host, {
    request() {
      requested = true;
      return Promise.resolve({});
    },
  });
  assert.equal(requested, false);
  assert.equal(host.html, undefined);
});

test("drawer projection follows the provided session context and stays collapsed", async () => {
  const host = new PanelHost();
  const calls = [];
  panel.default.slots["drawer.panel"].mount(host, {
    sessionId: "mobile:one",
    request(method, payload) {
      calls.push({ method, payload });
      return Promise.resolve({
        state: "pending",
        summary: "有 2 条消息待整理",
        pending_user_messages: 2,
        message_count: 14,
        last_consolidated_preview: "上一次已经整理到这里",
      });
    },
  });
  await new Promise((resolve) => setImmediate(resolve));

  assert.deepEqual(calls, [{ method: "memory.status", payload: undefined }]);
  assert.equal(host.trigger.dataset.state, "pending");
  assert.equal(host.summary.textContent, "有 2 条消息待整理");
  assert.equal(host.pendingValue.textContent, "2");
  assert.equal(host.totalValue.textContent, "14");
  assert.equal(host.previewText.textContent, "上一次已经整理到这里");
  assert.equal(host.detail.hidden, true);
  assert.equal(host.detail.inert, true);
});

test("detail leaves the accessibility tree while collapsed", async () => {
  const host = new PanelHost();
  panel.default.slots["drawer.panel"].mount(host, {
    sessionId: "mobile:two",
    request() {
      return Promise.resolve({
        state: "up_to_date",
        summary: "已整理到最新",
        pending_user_messages: 0,
        message_count: 8,
        last_consolidated_preview: null,
      });
    },
  });
  await new Promise((resolve) => setImmediate(resolve));

  host.trigger.click();
  assert.equal(host.trigger.getAttribute("aria-expanded"), "true");
  assert.equal(host.detail.getAttribute("aria-hidden"), "false");
  assert.equal(host.detail.hidden, false);
  assert.equal(host.detail.inert, false);
  host.trigger.click();
  assert.equal(host.detail.getAttribute("aria-hidden"), "true");
  assert.equal(host.detail.hidden, true);
  assert.equal(host.detail.inert, true);
});

test("opening the same session refreshes the projection in place", async () => {
  const host = new PanelHost();
  const responses = [
    {
      state: "pending",
      summary: "有 2 条消息待整理",
      pending_user_messages: 2,
      message_count: 10,
      last_consolidated_preview: "旧进度",
    },
    {
      state: "up_to_date",
      summary: "已整理到最新",
      pending_user_messages: 0,
      message_count: 12,
      last_consolidated_preview: "新进度",
    },
  ];
  let requests = 0;
  panel.default.slots["drawer.panel"].mount(host, {
    sessionId: "mobile:same",
    request() {
      const response = responses[requests];
      requests += 1;
      return Promise.resolve(response);
    },
  });
  await new Promise((resolve) => setImmediate(resolve));
  host.trigger.click();
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(requests, 2);
  assert.equal(host.summary.textContent, "已整理到最新");
  assert.equal(host.pendingValue.textContent, "0");
  assert.equal(host.totalValue.textContent, "12");
  assert.equal(host.previewText.textContent, "新进度");
  assert.equal(host.detail.hidden, false);
});

test("a late refresh cannot overwrite a newer request generation", async () => {
  const host = new PanelHost();
  const pending = [];
  panel.default.slots["drawer.panel"].mount(host, {
    sessionId: "mobile:same",
    request() {
      return new Promise((resolve) => pending.push(resolve));
    },
  });
  pending[0]({
    state: "pending",
    summary: "初始",
    pending_user_messages: 2,
    message_count: 10,
    last_consolidated_preview: null,
  });
  await new Promise((resolve) => setImmediate(resolve));
  host.trigger.click();
  host.trigger.click();
  host.trigger.click();
  assert.equal(pending.length, 3);
  pending[2]({
    state: "up_to_date",
    summary: "最新响应",
    pending_user_messages: 0,
    message_count: 12,
    last_consolidated_preview: null,
  });
  await new Promise((resolve) => setImmediate(resolve));
  pending[1]({
    state: "pending",
    summary: "迟到响应",
    pending_user_messages: 1,
    message_count: 11,
    last_consolidated_preview: null,
  });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(host.summary.textContent, "最新响应");
  assert.equal(host.totalValue.textContent, "12");
});

test("unavailable session is neutral and cannot expand", async () => {
  const host = new PanelHost();
  panel.default.slots["drawer.panel"].mount(host, {
    sessionId: "mobile:deleted",
    request() {
      return Promise.resolve({
        state: "unavailable",
        summary: "电脑端已不存在",
        pending_user_messages: 0,
        message_count: 0,
        last_consolidated_preview: null,
      });
    },
  });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(host.summary.textContent, "电脑端已不存在");
  assert.equal(host.trigger.disabled, true);
  assert.equal(host.pending.hidden, true);
  assert.equal(host.chevron.hidden, true);
  assert.equal(host.detail.hidden, true);
});
