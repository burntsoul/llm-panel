// DOM helpers
const qs = (sel, root = document) => root.querySelector(sel);
const qsa = (sel, root = document) => Array.from(root.querySelectorAll(sel));

// Tab state + hash routing
const VALID_TABS = ["llm", "image", "links", "chat", "logs"];
let activeTab = "llm";
let statusTimer = null;
let logsTimer = null;
let llmTimer = null;

function normalizeHash(hashValue) {
  const value = (hashValue || "").replace(/^#/, "").toLowerCase();
  return VALID_TABS.includes(value) ? value : "llm";
}

function setHash(tabId) {
  if (window.location.hash !== `#${tabId}`) {
    window.location.hash = tabId;
  }
}

function activateTab(tabId) {
  const safeTab = normalizeHash(tabId);
  activeTab = safeTab;

  qsa(".main-tab").forEach((tabBtn) => {
    tabBtn.classList.toggle("active", tabBtn.dataset.tab === safeTab);
  });

  qsa(".tab-panel").forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.panel === safeTab);
  });

  syncTabPolling();
  return safeTab;
}

function handleMainTabClick(event) {
  const button = event.target.closest(".main-tab");
  if (!button) {
    return;
  }

  const tabId = normalizeHash(button.dataset.tab);
  activateTab(tabId);
  setHash(tabId);
}

function handleHashChange() {
  const tabId = normalizeHash(window.location.hash);
  const active = activateTab(tabId);
  if (active !== window.location.hash.replace(/^#/, "")) {
    setHash(active);
  }
}

// API helpers
async function getJson(url, options = undefined) {
  const resp = await fetch(url, options);
  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status}`);
  }
  return resp.json();
}

function formatIsoTime(value) {
  if (!value) {
    return "-";
  }
  try {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return value;
    }
    return date.toLocaleString();
  } catch (e) {
    return value;
  }
}

function setText(id, value) {
  const node = qs(`#${id}`);
  if (node) {
    node.textContent = value;
  }
}

function setStatusChip(id, isOk, okText = "UP", badText = "DOWN") {
  const node = qs(`#${id}`);
  if (!node) {
    return;
  }
  node.textContent = isOk ? okText : badText;
  node.classList.remove("status-ok", "status-bad");
  node.classList.add(isOk ? "status-ok" : "status-bad");
}

function formatMaybeNumber(value, suffix = "", digits = 0) {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "--";
  }
  const num = digits > 0 ? value.toFixed(digits) : Math.round(value).toString();
  return `${num}${suffix}`;
}

// LLM CONTROL tab logic
function initPowerButtons() {
  qsa("[data-power-action]").forEach((btn) => {
    btn.addEventListener("click", () => sendPower(btn.dataset.powerAction || ""));
  });

  const openModelsBtn = qs("#open-models-btn");
  if (openModelsBtn) {
    openModelsBtn.addEventListener("click", openModelsModal);
  }
}

// GPU watchdog panel
let watchdogEls = {
  enableBtn: null,
  disableBtn: null,
  resetBtn: null,
  actionStatus: null,
};

function initWatchdogPanel() {
  watchdogEls = {
    enableBtn: qs("#watchdog-enable-btn"),
    disableBtn: qs("#watchdog-disable-btn"),
    resetBtn: qs("#watchdog-reset-btn"),
    actionStatus: qs("#watchdog-action-status"),
  };

  if (watchdogEls.enableBtn) {
    watchdogEls.enableBtn.addEventListener("click", () => sendWatchdogControl({ enabled: true }, "Watchdog enabled."));
  }
  if (watchdogEls.disableBtn) {
    watchdogEls.disableBtn.addEventListener("click", () => sendWatchdogControl({ enabled: false }, "Watchdog disabled."));
  }
  if (watchdogEls.resetBtn) {
    watchdogEls.resetBtn.addEventListener("click", () => sendWatchdogControl({ reset_error: true }, "Watchdog error reset."));
  }
}

function setWatchdogButtonsDisabled(disabled) {
  if (watchdogEls.enableBtn) watchdogEls.enableBtn.disabled = disabled;
  if (watchdogEls.disableBtn) watchdogEls.disableBtn.disabled = disabled;
  if (watchdogEls.resetBtn) watchdogEls.resetBtn.disabled = disabled;
}

function setWatchdogButtonStateFromStatus(wd) {
  if (!wd || typeof wd.enabled !== "boolean") {
    return;
  }
  if (watchdogEls.enableBtn) watchdogEls.enableBtn.disabled = wd.enabled;
  if (watchdogEls.disableBtn) watchdogEls.disableBtn.disabled = !wd.enabled;
  if (watchdogEls.resetBtn) watchdogEls.resetBtn.disabled = false;
}

function setWatchdogActionStatus(message, isError = false) {
  if (!watchdogEls.actionStatus) {
    return;
  }
  watchdogEls.actionStatus.textContent = message;
  watchdogEls.actionStatus.classList.toggle("status-error", !!isError);
  watchdogEls.actionStatus.classList.toggle("status-ok-text", !isError);
}

function updateWatchdogPanelFromData(wd, gpuFallback) {
  const enabledText = wd && typeof wd.enabled === "boolean" ? (wd.enabled ? "YES" : "NO") : "--";
  const mode = wd && wd.mode ? String(wd.mode).toUpperCase() : "--";
  const telemetryOk =
    wd && typeof wd.telemetry_ok === "boolean"
      ? wd.telemetry_ok
        ? "OK"
        : "ERROR"
      : gpuFallback && typeof gpuFallback.telemetry_ok === "boolean"
        ? gpuFallback.telemetry_ok
          ? "OK"
          : "ERROR"
        : "--";

  const gpuName = wd && wd.gpu_name ? wd.gpu_name : gpuFallback && gpuFallback.gpu_name ? gpuFallback.gpu_name : "--";
  const gpuId = wd && wd.gpu_id ? wd.gpu_id : gpuFallback && gpuFallback.gpu_id ? gpuFallback.gpu_id : "--";
  const gpuTemp = wd && typeof wd.gpu_temp_c === "number" ? wd.gpu_temp_c : gpuFallback && typeof gpuFallback.gpu_temp_c === "number" ? gpuFallback.gpu_temp_c : null;
  const gpuUtil = wd && typeof wd.gpu_util_percent === "number" ? wd.gpu_util_percent : gpuFallback && typeof gpuFallback.gpu_util_percent === "number" ? gpuFallback.gpu_util_percent : null;
  const gpuMem = wd && typeof wd.gpu_mem_util_percent === "number" ? wd.gpu_mem_util_percent : gpuFallback && typeof gpuFallback.gpu_mem_util_percent === "number" ? gpuFallback.gpu_mem_util_percent : null;

  setText("wd-enabled", enabledText);
  setText("wd-mode", mode);
  setText("wd-telemetry-ok", telemetryOk);
  setText("wd-gpu-name-id", `${gpuName} / ${gpuId}`);
  setText("wd-gpu-temp", formatMaybeNumber(gpuTemp, " \u00b0C"));
  setText("wd-gpu-util", formatMaybeNumber(gpuUtil, "%"));
  setText("wd-gpu-mem-util", formatMaybeNumber(gpuMem, "%"));
  setText("wd-last-target", wd && wd.last_target_xx !== null && wd.last_target_xx !== undefined ? String(wd.last_target_xx) : "--");
  setText("wd-last-applied", wd && wd.last_applied_xx !== null && wd.last_applied_xx !== undefined ? String(wd.last_applied_xx) : "--");
  setText(
    "wd-last-command-ok",
    wd && typeof wd.last_command_ok === "boolean" ? (wd.last_command_ok ? "OK" : "FAIL") : "--",
  );
  setText("wd-last-command-at", formatIsoTime(wd && wd.last_command_at));
  setText("wd-updated-at", formatIsoTime((wd && wd.updated_at) || (gpuFallback && gpuFallback.updated_at)));
  setText("wd-last-error", wd && wd.last_error ? String(wd.last_error) : gpuFallback && gpuFallback.error ? String(gpuFallback.error) : "--");

  const poll = wd && wd.poll_seconds !== undefined ? `${wd.poll_seconds}s` : "--";
  const hysteresis = wd && wd.hysteresis_c !== undefined ? `${wd.hysteresis_c}\u00b0C` : "--";
  const failsafe = wd && wd.failsafe_fan_min_xx !== undefined ? String(wd.failsafe_fan_min_xx) : "--";
  const minChange = wd && wd.min_change_interval_seconds !== undefined ? `${wd.min_change_interval_seconds}s` : "--";
  const thresholds = wd && wd.thresholds ? wd.thresholds : "--";
  setText(
    "wd-settings-summary",
    `Poll/hysteresis/failsafe/min-change: ${poll} / ${hysteresis} / ${failsafe} / ${minChange} | thresholds: ${thresholds}`,
  );
}

async function refreshWatchdogPanel() {
  const [wdResult, gpuResult] = await Promise.allSettled([
    getJson("/api/gpu_watchdog/status"),
    getJson("/api/gpu_telemetry"),
  ]);

  const wd = wdResult.status === "fulfilled" ? wdResult.value : null;
  const gpu = gpuResult.status === "fulfilled" ? gpuResult.value : null;

  if (!wd && !gpu) {
    updateWatchdogPanelFromData(null, null);
    setWatchdogActionStatus("Watchdog and telemetry data unavailable.", true);
    setWatchdogButtonsDisabled(true);
    return;
  }

  updateWatchdogPanelFromData(wd, gpu);

  if (!wd) {
    setWatchdogActionStatus("Watchdog status unavailable. Showing telemetry fallback.", true);
    setWatchdogButtonsDisabled(true);
    return;
  }

  setWatchdogButtonsDisabled(false);
  setWatchdogButtonStateFromStatus(wd);
}

async function sendWatchdogControl(payload, successMessage) {
  setWatchdogButtonsDisabled(true);
  setWatchdogActionStatus("Sending watchdog command...");

  try {
    const resp = await getJson("/api/gpu_watchdog/control", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });

    if (!resp.ok) {
      setWatchdogActionStatus(`Command failed: ${resp.error || "unknown error"}`, true);
    } else {
      setWatchdogActionStatus(successMessage, false);
    }
  } catch (err) {
    setWatchdogActionStatus(`Command failed: ${err}`, true);
  }

  try {
    await refreshWatchdogPanel();
  } catch (_) {
    // Keep action feedback even if refresh fails.
  }
}

async function sendPower(action) {
  if (!action) {
    return;
  }

  try {
    openModal("Virta-komento", `<p>Lahetetaan komentoa <b>${action}</b>...</p>`);

    const formData = new FormData();
    formData.append("action", action);

    const data = await getJson("/power_json", {
      method: "POST",
      body: formData,
    });

    const msg = data.message || "(ei viestia)";
    const powerNow = data.power || "tuntematon";
    const ok = data.ok === undefined ? true : !!data.ok;

    let bodyHtml = `<p>${msg}</p><p>Nykyinen virran tila: <b>${powerNow}</b></p>`;
    if (!ok) {
      bodyHtml += "<p style='color:#c43c2b;'>Komento ei ehka toteutunut kokonaan.</p>";
    }

    openModal("Virta-komento", bodyHtml);
    await refreshStatus();
  } catch (err) {
    openModal("Virhe", `<p>Virta-komento epaonnistui: ${err}</p>`);
  }
}

async function openModelsModal() {
  try {
    const data = await getJson("/api/models");
    const rows = data.models || [];

    let html =
      "<table style='border-collapse:collapse;width:100%;'><thead><tr>" +
      "<th style='text-align:left;padding:4px 8px;border-bottom:1px solid #e5e7eb;'>Model ID</th>" +
      "<th style='text-align:left;padding:4px 8px;border-bottom:1px solid #e5e7eb;'>Source</th>" +
      "<th style='text-align:left;padding:4px 8px;border-bottom:1px solid #e5e7eb;'>Device</th>" +
      "<th style='text-align:left;padding:4px 8px;border-bottom:1px solid #e5e7eb;'>Tilanne nyt</th>" +
      "<th style='text-align:left;padding:4px 8px;border-bottom:1px solid #e5e7eb;'>Label</th>" +
      "</tr></thead><tbody>";

    rows.forEach((row) => {
      let status = "Tuntematon";
      if (row.present_now === true) {
        status = "Ollamassa";
      } else if (row.present_now === false) {
        status = "Ei Ollamassa";
      }

      html +=
        "<tr>" +
        `<td style='padding:4px 8px;border-bottom:1px solid #f3f4f6;'>${row.id || ""}</td>` +
        `<td style='padding:4px 8px;border-bottom:1px solid #f3f4f6;'>${row.source || ""}</td>` +
        `<td style='padding:4px 8px;border-bottom:1px solid #f3f4f6;'>${row.device || ""}</td>` +
        `<td style='padding:4px 8px;border-bottom:1px solid #f3f4f6;'>${status}</td>` +
        `<td style='padding:4px 8px;border-bottom:1px solid #f3f4f6;'>${row.label || ""}</td>` +
        "</tr>";
    });

    html += "</tbody></table>";
    openModal("Mallit ja tila", html);
  } catch (err) {
    openModal("Virhe", `<p>Mallilistan haku epaonnistui: ${err}</p>`);
  }
}

// IMAGE ENGINE tab logic
function initImageControls() {
  const wakeBtn = qs("#wake-comfyui-btn");
  if (!wakeBtn) {
    return;
  }

  wakeBtn.addEventListener("click", wakeComfyUI);
}

async function wakeComfyUI() {
  try {
    const data = await getJson("/api/comfyui_wake", { method: "POST" });
    if (data.ok) {
      openModal("ComfyUI", "<p>ComfyUI kaynnistys OK.</p>");
    } else {
      openModal(
        "ComfyUI",
        `<p>ComfyUI kaynnistys epaonnistui: ${data.error || "tuntematon virhe"}</p>`,
      );
    }
    await refreshStatus();
  } catch (err) {
    openModal("Virhe", `<p>ComfyUI kaynnistys epaonnistui: ${err}</p>`);
  }
}

// CHAT tab logic
let chatEls = {
  container: null,
  prompt: null,
  send: null,
  status: null,
  model: null,
};

function initChat() {
  chatEls = {
    container: qs("#chat-container"),
    prompt: qs("#prompt"),
    send: qs("#send-btn"),
    status: qs("#status-line"),
    model: qs("#model"),
  };

  if (chatEls.send) {
    chatEls.send.addEventListener("click", sendMessage);
  }

  if (chatEls.prompt) {
    chatEls.prompt.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        sendMessage();
      }
    });
  }
}

function appendMessage(text, role) {
  if (!chatEls.container) {
    return null;
  }

  const div = document.createElement("div");
  div.classList.add("msg");
  if (role === "user") div.classList.add("msg-user");
  if (role === "assistant") div.classList.add("msg-assistant");
  if (role === "system") div.classList.add("msg-system");
  div.textContent = text;
  chatEls.container.appendChild(div);
  chatEls.container.scrollTop = chatEls.container.scrollHeight;
  return div;
}

async function sendMessage() {
  if (!chatEls.prompt || !chatEls.model || !chatEls.send) {
    return;
  }

  const prompt = chatEls.prompt.value.trim();
  const model = chatEls.model.value;
  if (!prompt || !model || chatEls.model.disabled) {
    return;
  }

  appendMessage(prompt, "user");
  chatEls.prompt.value = "";
  chatEls.prompt.focus();

  const assistantDiv = appendMessage("", "assistant");
  if (!assistantDiv) {
    return;
  }

  chatEls.send.disabled = true;
  chatEls.model.disabled = true;
  if (chatEls.status) {
    chatEls.status.textContent = "Thinking...";
  }

  let assistantText = "";

  try {
    const formData = new FormData();
    formData.append("model", model);
    formData.append("prompt", prompt);

    const response = await fetch("/chat_stream", {
      method: "POST",
      body: formData,
    });

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let done = false;

    while (!done) {
      const result = await reader.read();
      done = result.done;

      if (result.value) {
        const chunk = decoder.decode(result.value, { stream: true });
        assistantText += chunk;

        const isAtBottom =
          chatEls.container.scrollHeight - chatEls.container.scrollTop - chatEls.container.clientHeight < 20;

        assistantDiv.textContent = assistantText;
        if (isAtBottom) {
          chatEls.container.scrollTop = chatEls.container.scrollHeight;
        }
      }
    }

    if (window.marked) {
      assistantDiv.innerHTML = window.marked.parse(assistantText);
    } else {
      assistantDiv.textContent = assistantText;
    }

    if (window.MathJax && window.MathJax.typesetPromise) {
      try {
        await window.MathJax.typesetPromise([assistantDiv]);
      } catch (e) {
        console.warn("MathJax error", e);
      }
    }

    if (chatEls.status) {
      chatEls.status.textContent = "";
    }
  } catch (err) {
    assistantDiv.textContent += `\n[Virhe: ${err}]`;
    if (chatEls.status) {
      chatEls.status.textContent = "Virhe pyynnossa.";
    }
  } finally {
    chatEls.send.disabled = false;
    chatEls.model.disabled = false;
  }
}

// LOGS tab logic
let logsEls = {
  viewer: null,
  lines: null,
  path: null,
  button: null,
};

function initLogs() {
  logsEls = {
    viewer: qs("#log-viewer"),
    lines: qs("#log-lines"),
    path: qs("#log-path"),
    button: qs("#refresh-logs-btn"),
  };

  if (logsEls.button) {
    logsEls.button.addEventListener("click", fetchLogs);
  }
}

async function fetchLogs() {
  try {
    const lineCount = logsEls.lines ? logsEls.lines.value : "200";
    const data = await getJson(`/api/logs?lines=${encodeURIComponent(lineCount)}`);

    if (!data.ok) {
      if (logsEls.viewer) {
        logsEls.viewer.textContent = data.error || "Logien luku epaonnistui.";
      }
      if (logsEls.path) {
        logsEls.path.textContent = data.path ? `Path: ${data.path}` : "";
      }
      return;
    }

    if (logsEls.viewer) {
      logsEls.viewer.textContent = (data.lines || []).join("\n");
    }
    if (logsEls.path) {
      logsEls.path.textContent = data.path ? `Path: ${data.path}` : "";
    }
  } catch (err) {
    if (logsEls.viewer) {
      logsEls.viewer.textContent = `Logien luku epaonnistui: ${err}`;
    }
  }
}

// Shared status bar updates
async function refreshStatus() {
  try {
    const data = await getJson("/api/status");

    setText("llm-vm-status", data.llm_vm || "-");
    setText("win-vm-status", data.windows_vm || "-");
    setText("maintenance-status", data.maintenance_mode ? "ON" : "OFF");

    setStatusChip("llm-api-status", !!data.llm_up);

    const healthNode = qs("#system-health");
    if (healthNode) {
      const health = data.system_health || "tuntematon";
      healthNode.textContent = health;
      healthNode.classList.remove("status-ok", "status-bad");
      const ok = typeof health === "string" ? health.toLowerCase().startsWith("ok") : false;
      healthNode.classList.add(ok ? "status-ok" : "status-bad");
    }

    setText("cpu-temp", data.cpu_temp || "-");

    setStatusChip("comfyui-status", !!data.comfyui_up);
    setText("comfyui-last-activity", formatIsoTime(data.comfyui_last_activity));
    setText("comfyui-last-error", data.comfyui_last_error || "-");

    setText("top-llm-vm", data.llm_vm || "--");
    setText("top-llm-api", data.llm_up ? "UP" : "DOWN");
    let watchdogMode = "--";
    if (typeof data.gpu_watchdog_mode === "string") {
      const normalized = data.gpu_watchdog_mode.toLowerCase();
      if (normalized === "disabled") watchdogMode = "OFF";
      else if (normalized === "auto") watchdogMode = "AUTO";
      else if (normalized === "failsafe") watchdogMode = "FAILSAFE";
      else watchdogMode = normalized.toUpperCase();
    }
    setText("top-watchdog", watchdogMode);

    try {
      const gpu = await getJson("/api/gpu_telemetry");
      const temp = gpu && typeof gpu.gpu_temp_c === "number" ? `${Math.round(gpu.gpu_temp_c)}\u00b0C` : "--";
      setText("top-gpu", temp);
      const topGpu = qs("#top-gpu");
      if (topGpu) {
        const util = gpu && typeof gpu.gpu_util_percent === "number" ? `${Math.round(gpu.gpu_util_percent)}%` : "--";
        const mem = gpu && typeof gpu.gpu_mem_util_percent === "number" ? `${Math.round(gpu.gpu_mem_util_percent)}%` : "--";
        topGpu.title = `GPU temp/util/mem: ${temp} / ${util} / ${mem}`;
      }
    } catch (_) {
      setText("top-gpu", "--");
    }
  } catch (err) {
    console.warn("Status-paivitys epaonnistui:", err);
  }
}

function initSubtabs() {
  qsa(".subtabs").forEach((subtabRow) => {
    subtabRow.addEventListener("click", (event) => {
      const button = event.target.closest(".subtab");
      if (!button) {
        return;
      }

      qsa(".subtab", subtabRow).forEach((subtab) => {
        subtab.classList.toggle("active", subtab === button);
      });
    });
  });
}

// Polling scheduler / intervals
function syncTabPolling() {
  if (activeTab === "logs") {
    if (!logsTimer) {
      fetchLogs();
      logsTimer = window.setInterval(() => {
        if (activeTab === "logs") {
          fetchLogs();
        }
      }, 10000);
    }
  } else if (logsTimer) {
    window.clearInterval(logsTimer);
    logsTimer = null;
  }

  if (activeTab === "llm") {
    if (!llmTimer) {
      refreshWatchdogPanel();
      llmTimer = window.setInterval(() => {
        if (activeTab === "llm") {
          refreshWatchdogPanel();
        }
      }, 4000);
    }
  } else if (llmTimer) {
    window.clearInterval(llmTimer);
    llmTimer = null;
  }
}

// App init
function initModal() {
  const overlay = qs("#modal-overlay");
  const closeBtn = qs("#close-modal-btn");

  if (closeBtn) {
    closeBtn.addEventListener("click", closeModal);
  }

  if (overlay) {
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) {
        closeModal();
      }
    });
  }
}

function openModal(title, bodyHtml) {
  const overlay = qs("#modal-overlay");
  const modalTitle = qs("#modal-title");
  const modalBody = qs("#modal-body");

  if (modalTitle) {
    modalTitle.textContent = title || "";
  }
  if (modalBody) {
    modalBody.innerHTML = bodyHtml || "";
  }
  if (overlay) {
    overlay.style.display = "flex";
  }
}

function closeModal() {
  const overlay = qs("#modal-overlay");
  if (overlay) {
    overlay.style.display = "none";
  }
}

function initApp() {
  const mainTabs = qs(".main-tabs");
  if (mainTabs) {
    mainTabs.addEventListener("click", handleMainTabClick);
  }

  initSubtabs();
  initModal();
  initPowerButtons();
  initWatchdogPanel();
  initImageControls();
  initChat();
  initLogs();

  handleHashChange();
  window.addEventListener("hashchange", handleHashChange);

  refreshStatus();
  statusTimer = window.setInterval(refreshStatus, 5000);
  syncTabPolling();
}

document.addEventListener("DOMContentLoaded", initApp);
