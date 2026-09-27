function formatCount(value) {
  return new Intl.NumberFormat("zh-CN").format(Number(value || 0));
}

function renderPanel(host) {
  host.innerHTML = `
    <button class="status-memory-trigger" type="button" aria-expanded="false">
      <span class="status-memory-copy">
        <span class="status-memory-label">记忆整理</span>
        <strong></strong>
      </span>
      <span class="status-memory-pending"></span>
      <span class="status-memory-chevron" aria-hidden="true"></span>
    </button>
    <div class="status-memory-detail" hidden aria-hidden="true" inert>
      <dl class="status-memory-metrics">
        <div><dt>尚未整理</dt><dd data-value="pending"></dd></div>
        <div><dt>会话消息</dt><dd data-value="total"></dd></div>
      </dl>
      <div class="status-memory-preview" hidden>
        <span>最后已整理</span>
        <p></p>
      </div>
    </div>`;
}

function applyStatus(host, status) {
  const trigger = host.querySelector(".status-memory-trigger");
  const detail = host.querySelector(".status-memory-detail");
  const pending = trigger.querySelector(".status-memory-pending");
  const chevron = trigger.querySelector(".status-memory-chevron");
  const preview = host.querySelector(".status-memory-preview");
  const unavailable = status.state === "unavailable";
  trigger.dataset.state = String(status.state || "never");
  trigger.disabled = unavailable;
  trigger.setAttribute("aria-busy", "false");
  trigger.querySelector("strong").textContent = String(status.summary || "无法读取整理状态");
  pending.textContent = `${formatCount(status.pending_user_messages)} 条待整理`;
  pending.hidden = unavailable;
  chevron.hidden = unavailable;
  detail.querySelector('[data-value="pending"]').textContent = formatCount(status.pending_user_messages);
  detail.querySelector('[data-value="total"]').textContent = formatCount(status.message_count);
  const previewText = status.last_consolidated_preview;
  preview.hidden = !previewText;
  preview.querySelector("p").textContent = previewText ? String(previewText) : "";
  if (unavailable) {
    trigger.setAttribute("aria-expanded", "false");
    detail.hidden = true;
    detail.setAttribute("aria-hidden", "true");
    detail.inert = true;
    host.classList.toggle("is-expanded", false);
  }
}

function memoryStatusPanel(host, context) {
  let active = true;
  let requestGeneration = 0;
  host.className += " status-memory-panel";
  if (!context.sessionId) return () => { active = false; };
  host.innerHTML = '<div class="status-memory-loading" role="status">正在读取记忆整理状态…</div>';

  function showError(error) {
    host.innerHTML = "";
    const message = document.createElement("p");
    message.className = "status-memory-error";
    message.setAttribute("role", "status");
    message.textContent = error instanceof Error
      ? `记忆整理状态不可用：${error.message}`
      : "记忆整理状态不可用";
    host.append(message);
  }

  function requestStatus(onCurrent) {
    const generation = ++requestGeneration;
    context.query("memory.status").then((status) => {
      if (!active || generation !== requestGeneration) return;
      onCurrent(status);
    }).catch((error) => {
      if (!active || generation !== requestGeneration) return;
      showError(error);
    });
  }

  requestStatus((status) => {
    renderPanel(host);
    const trigger = host.querySelector(".status-memory-trigger");
    const detail = host.querySelector(".status-memory-detail");
    applyStatus(host, status);
    trigger.addEventListener("click", () => {
      const expanded = trigger.getAttribute("aria-expanded") !== "true";
      trigger.setAttribute("aria-expanded", String(expanded));
      detail.hidden = !expanded;
      detail.setAttribute("aria-hidden", String(!expanded));
      detail.inert = !expanded;
      host.classList.toggle("is-expanded", expanded);
      if (!expanded) return;
      trigger.setAttribute("aria-busy", "true");
      requestStatus((latestStatus) => applyStatus(host, latestStatus));
    });
  });
  return () => {
    active = false;
    requestGeneration += 1;
  };
}

export default {
  slots: {
    "drawer.panel": { mount: memoryStatusPanel },
  },
};
