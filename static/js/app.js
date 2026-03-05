// DOM helpers
const qs = (sel, root = document) => root.querySelector(sel);
const qsa = (sel, root = document) => Array.from(root.querySelectorAll(sel));

// Tab state + hash routing
const VALID_TABS = ["llm", "image", "links", "chat", "logs"];
const SUBTAB_OPTIONS = {
  llm: ["overview", "vm-controls", "thermal", "health", "models"],
  image: ["main", "image-edit", "future"],
  links: ["main", "placeholder-a", "placeholder-b"],
  chat: ["main", "placeholder-a", "placeholder-b"],
  logs: ["main", "placeholder-a", "placeholder-b"],
};

const LLM_SECTION_TARGETS = {
  overview: "llm-section-overview",
  "vm-controls": "llm-section-vm-controls",
  thermal: "llm-section-thermal",
  health: "llm-section-health",
  models: "llm-section-models",
};

let activeTab = "llm";
let activeSubtabs = {
  llm: "overview",
  image: "main",
  links: "main",
  chat: "main",
  logs: "main",
};

let statusTimer = null;
let logsTimer = null;
let llmTimer = null;
let previousGpuTemp = null;

function getDefaultSubtab(tab) {
  const options = SUBTAB_OPTIONS[tab] || ["main"];
  return options[0];
}

function normalizeHashTab(value) {
  const tab = (value || "").toLowerCase();
  return VALID_TABS.includes(tab) ? tab : "llm";
}

function normalizeSubtab(tab, value) {
  const options = SUBTAB_OPTIONS[tab] || ["main"];
  const candidate = (value || "").toLowerCase();
  if (options.includes(candidate)) return candidate;
  if (options.includes(activeSubtabs[tab])) return activeSubtabs[tab];
  return getDefaultSubtab(tab);
}

function parseHash() {
  const raw = (window.location.hash || "").replace(/^#/, "").trim();
  if (!raw) {
    return { tab: "llm", subtab: null, hasSubtab: false };
  }

  const [tabPart, subPart] = raw.split("/");
  return {
    tab: normalizeHashTab(tabPart),
    subtab: subPart ? subPart.toLowerCase() : null,
    hasSubtab: !!subPart,
  };
}

function buildHash(tab, subtab) {
  const safeTab = normalizeHashTab(tab);
  const safeSub = normalizeSubtab(safeTab, subtab);
  const defaultSub = getDefaultSubtab(safeTab);
  if (safeSub && safeSub !== defaultSub) {
    return `#${safeTab}/${safeSub}`;
  }
  return `#${safeTab}`;
}

function setHash(tab, subtab) {
  const next = buildHash(tab, subtab);
  if (window.location.hash !== next) {
    window.location.hash = next;
  }
}

function activateTab(tabId, subtabId = null, opts = {}) {
  const { pushHash = false, scrollSubtab = false } = opts;
  const safeTab = normalizeHashTab(tabId);
  activeTab = safeTab;

  qsa(".main-tab").forEach((tabBtn) => {
    tabBtn.classList.toggle("active", tabBtn.dataset.tab === safeTab);
  });

  qsa(".tab-panel").forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.panel === safeTab);
  });

  activateSubtab(safeTab, subtabId, { pushHash: false, scroll: scrollSubtab });
  syncTabPolling();

  if (pushHash) {
    setHash(safeTab, activeSubtabs[safeTab]);
  }
}

function handleMainTabClick(event) {
  const button = event.target.closest(".main-tab");
  if (!button) return;
  activateTab(button.dataset.tab || "llm", null, { pushHash: true, scrollSubtab: false });
}

function handleHashChange() {
  const hash = parseHash();
  activateTab(hash.tab, hash.subtab, { pushHash: false, scrollSubtab: hash.hasSubtab });

  const canonical = buildHash(activeTab, activeSubtabs[activeTab]);
  if (window.location.hash !== canonical) {
    window.location.hash = canonical;
  }
}

// Subtab state and routing
function activateSubtab(tabId, subtabId, opts = {}) {
  const { pushHash = false, scroll = true } = opts;
  const safeTab = normalizeHashTab(tabId);
  const safeSub = normalizeSubtab(safeTab, subtabId);
  activeSubtabs[safeTab] = safeSub;

  const tabPanel = qs(`.tab-panel[data-panel="${safeTab}"]`);
  if (!tabPanel) return;

  const subtabRow = qs(`.subtabs[data-subtab-group="${safeTab}"]`, tabPanel);
  if (subtabRow) {
    qsa(".subtab", subtabRow).forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.subtab === safeSub);
    });
  }

  if (safeTab === "llm") {
    const sectionsPanel = qs('.subtab-panel[data-subtab-panel="sections"]', tabPanel);
    if (sectionsPanel) {
      qsa(".subtab-panel", tabPanel).forEach((panel) => panel.classList.remove("active"));
      sectionsPanel.classList.add("active");
    }

    if (scroll) {
      const targetId = LLM_SECTION_TARGETS[safeSub];
      const target = targetId ? qs(`#${targetId}`) : null;
      if (target) {
        target.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    }
  } else {
    qsa(".subtab-panel", tabPanel).forEach((panel) => {
      panel.classList.toggle("active", panel.dataset.subtabPanel === safeSub);
    });
  }

  if (pushHash) {
    setHash(safeTab, safeSub);
  }
}

function initSubtabs() {
  qsa(".subtabs").forEach((row) => {
    row.addEventListener("click", (event) => {
      const btn = event.target.closest(".subtab");
      if (!btn) return;

      const tabPanel = btn.closest(".tab-panel");
      if (!tabPanel) return;

      const tab = tabPanel.dataset.panel;
      const sub = btn.dataset.subtab;
      activateSubtab(tab, sub, { pushHash: true, scroll: true });
    });
  });
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
  if (!value) return "-";
  try {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return date.toLocaleString();
  } catch (_) {
    return value;
  }
}

// Shared UI helpers
function setText(id, value) {
  const node = qs(`#${id}`);
  if (node) node.textContent = value;
}

function formatMaybeNumber(value, suffix = "", digits = 0) {
  if (typeof value !== "number" || Number.isNaN(value)) return "--";
  const num = digits > 0 ? value.toFixed(digits) : Math.round(value).toString();
  return `${num}${suffix}`;
}

// Status normalization + badge mapping
const TONES = ["tone-good", "tone-warn", "tone-bad", "tone-neutral"];

function applyTone(node, tone) {
  if (!node) return;
  node.classList.remove(...TONES, "status-ok", "status-bad");
  node.classList.add(`tone-${tone}`);
}

function renderBadge(id, text, tone, title = "") {
  const node = qs(`#${id}`);
  if (!node) return;
  node.textContent = text;
  applyTone(node, tone);
  if (title) node.title = title;
}

function normalizeVmState(raw) {
  const value = String(raw || "").toLowerCase();
  if (!value || value === "--" || value === "unknown") return { text: "unknown", tone: "neutral" };
  if (value.includes("running")) return { text: "RUNNING", tone: "good" };
  if (value.includes("starting")) return { text: "STARTING", tone: "warn" };
  if (value.includes("stopping")) return { text: "STOPPING", tone: "warn" };
  if (value.includes("stopped") || value.includes("off")) return { text: "OFF", tone: "bad" };
  if (value.includes("error")) return { text: "FAIL", tone: "bad" };
  return { text: String(raw), tone: "neutral" };
}

function normalizeApiState(llmUp) {
  if (typeof llmUp !== "boolean") return { text: "unknown", tone: "neutral" };
  return llmUp ? { text: "OK", tone: "good" } : { text: "FAIL", tone: "bad" };
}

function normalizeWatchdogMode(mode) {
  const value = String(mode || "").toLowerCase();
  if (!value) return { text: "--", tone: "neutral" };
  if (value === "disabled") return { text: "OFF", tone: "neutral" };
  if (value === "auto") return { text: "AUTO", tone: "good" };
  if (value === "vm_off_idle") return { text: "VM OFF IDLE", tone: "neutral" };
  if (value === "failsafe") return { text: "FAILSAFE", tone: "bad" };
  return { text: value.toUpperCase(), tone: "neutral" };
}

function normalizeComfyState(up) {
  if (typeof up !== "boolean") return { text: "unknown", tone: "neutral" };
  return up ? { text: "ON", tone: "good" } : { text: "OFF", tone: "bad" };
}

function normalizeHealthState(healthRaw) {
  const health = String(healthRaw || "").trim();
  if (!health) return { text: "unknown", tone: "neutral" };
  if (health.toLowerCase().startsWith("ok")) return { text: health, tone: "good" };
  return { text: health, tone: "warn" };
}

// GPU trend tracking
function gpuTrendArrow(currentTemp) {
  if (typeof currentTemp !== "number" || Number.isNaN(currentTemp)) {
    return "";
  }

  if (typeof previousGpuTemp !== "number") {
    previousGpuTemp = currentTemp;
    return "\u2192";
  }

  const delta = currentTemp - previousGpuTemp;
  previousGpuTemp = currentTemp;
  if (delta > 0.4) return "\u2191";
  if (delta < -0.4) return "\u2193";
  return "\u2192";
}

function normalizeGpuBadge(gpu) {
  if (!gpu || typeof gpu.gpu_temp_c !== "number") {
    return { text: "--", tone: "neutral", title: "GPU telemetry unavailable" };
  }

  const temp = Math.round(gpu.gpu_temp_c);
  const trend = gpuTrendArrow(gpu.gpu_temp_c);
  const util = typeof gpu.gpu_util_percent === "number" ? `${Math.round(gpu.gpu_util_percent)}%` : "--";
  const mem = typeof gpu.gpu_mem_util_percent === "number" ? `${Math.round(gpu.gpu_mem_util_percent)}%` : "--";

  let tone = "good";
  if (temp >= 80) tone = "bad";
  else if (temp >= 70) tone = "warn";

  return {
    text: `${temp}\u00b0C ${trend}`,
    tone,
    title: `GPU temp/util/mem: ${temp}\u00b0C / ${util} / ${mem}`,
  };
}

// Status bar rendering
async function refreshStatus() {
  const [statusResult, gpuResult] = await Promise.allSettled([getJson("/api/status"), getJson("/api/gpu_telemetry")]);

  const statusData = statusResult.status === "fulfilled" ? statusResult.value : null;
  const gpuData = gpuResult.status === "fulfilled" ? gpuResult.value : null;

  if (!statusData) {
    renderBadge("top-llm-vm", "unknown", "neutral");
    renderBadge("top-api", "unknown", "neutral");
    renderBadge("top-watchdog", "--", "neutral");
    renderBadge("top-comfyui", "unknown", "neutral");
    setText("top-last-refresh", "error");
    return;
  }

  // Existing panel values
  setText("llm-vm-status", statusData.llm_vm || "-");
  setText("win-vm-status", statusData.windows_vm || "-");
  setText("maintenance-status", statusData.maintenance_mode ? "ON" : "OFF");

  const apiState = normalizeApiState(statusData.llm_up);
  renderBadge("llm-api-status", apiState.text, apiState.tone);

  const healthState = normalizeHealthState(statusData.system_health);
  renderBadge("system-health", healthState.text, healthState.tone);

  setText("cpu-temp", statusData.cpu_temp || "-");

  const comfyState = normalizeComfyState(!!statusData.comfyui_up);
  renderBadge("comfyui-status", comfyState.text, comfyState.tone);
  setText("comfyui-last-activity", formatIsoTime(statusData.comfyui_last_activity));
  setText("comfyui-last-error", statusData.comfyui_last_error || "-");

  // Top status bar values
  const vmState = normalizeVmState(statusData.llm_vm);
  renderBadge("top-llm-vm", vmState.text, vmState.tone, String(statusData.llm_vm || "unknown"));
  renderBadge("top-api", apiState.text, apiState.tone);

  const wdState = normalizeWatchdogMode(statusData.gpu_watchdog_mode);
  renderBadge("top-watchdog", wdState.text, wdState.tone);

  renderBadge("top-comfyui", comfyState.text, comfyState.tone);

  const gpuBadge = normalizeGpuBadge(gpuData);
  renderBadge("top-gpu", gpuBadge.text, gpuBadge.tone, gpuBadge.title);

  setText("top-last-refresh", new Date().toLocaleTimeString());
}

// LLM CONTROL section navigation (subtabs)
function initLlmSectionNavigation() {
  // No additional listeners needed; subtab click routing handles scroll.
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

async function sendPower(action) {
  if (!action) return;

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
      if (row.present_now === true) status = "Ollamassa";
      else if (row.present_now === false) status = "Ei Ollamassa";

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
  if (!wd || typeof wd.enabled !== "boolean") return;
  if (watchdogEls.enableBtn) watchdogEls.enableBtn.disabled = wd.enabled;
  if (watchdogEls.disableBtn) watchdogEls.disableBtn.disabled = !wd.enabled;
  if (watchdogEls.resetBtn) watchdogEls.resetBtn.disabled = false;
}

function setWatchdogActionStatus(message, isError = false) {
  if (!watchdogEls.actionStatus) return;
  watchdogEls.actionStatus.textContent = message;
  watchdogEls.actionStatus.classList.toggle("status-error", !!isError);
  watchdogEls.actionStatus.classList.toggle("status-ok-text", !isError);
}

function updateWatchdogPanelFromData(wd, gpuFallback) {
  const enabledText = wd && typeof wd.enabled === "boolean" ? (wd.enabled ? "YES" : "NO") : "--";
  const mode = wd && wd.mode ? normalizeWatchdogMode(wd.mode).text : "--";
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
  setText("wd-last-command-ok", wd && typeof wd.last_command_ok === "boolean" ? (wd.last_command_ok ? "OK" : "FAIL") : "--");
  setText("wd-last-command-at", formatIsoTime(wd && wd.last_command_at));
  setText("wd-updated-at", formatIsoTime((wd && wd.updated_at) || (gpuFallback && gpuFallback.updated_at)));
  setText("wd-last-error", wd && wd.last_error ? String(wd.last_error) : gpuFallback && gpuFallback.error ? String(gpuFallback.error) : "--");

  const poll = wd && wd.poll_seconds !== undefined ? `${wd.poll_seconds}s` : "--";
  const hysteresis = wd && wd.hysteresis_c !== undefined ? `${wd.hysteresis_c}\u00b0C` : "--";
  const failsafe = wd && wd.failsafe_fan_min_xx !== undefined ? String(wd.failsafe_fan_min_xx) : "--";
  const minChange = wd && wd.min_change_interval_seconds !== undefined ? `${wd.min_change_interval_seconds}s` : "--";
  const thresholds = wd && wd.thresholds ? wd.thresholds : "--";
  setText("wd-settings-summary", `Poll/hysteresis/failsafe/min-change: ${poll} / ${hysteresis} / ${failsafe} / ${minChange} | thresholds: ${thresholds}`);
}

async function refreshWatchdogPanel() {
  const [wdResult, gpuResult] = await Promise.allSettled([getJson("/api/gpu_watchdog/status"), getJson("/api/gpu_telemetry")]);

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
      headers: { "Content-Type": "application/json" },
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

// IMAGE ENGINE tab logic
function initImageControls() {
  const wakeBtn = qs("#wake-comfyui-btn");
  if (wakeBtn) {
    wakeBtn.addEventListener("click", wakeComfyUI);
  }
}

async function wakeComfyUI() {
  try {
    const data = await getJson("/api/comfyui_wake", { method: "POST" });
    if (data.ok) {
      openModal("ComfyUI", "<p>ComfyUI kaynnistys OK.</p>");
    } else {
      openModal("ComfyUI", `<p>ComfyUI kaynnistys epaonnistui: ${data.error || "tuntematon virhe"}</p>`);
    }
    await refreshStatus();
  } catch (err) {
    openModal("Virhe", `<p>ComfyUI kaynnistys epaonnistui: ${err}</p>`);
  }
}

// Image Edit Tool integration
const imageEditState = {
  drawing: false,
  currentImageDataUrl: null,
  history: [],
  historyLimit: 5,
};

let imageEditEls = {
  imageInput: null,
  imageCanvas: null,
  maskCanvas: null,
  brushSizeInput: null,
  clearMaskBtn: null,
  sendBtn: null,
  statusEl: null,
  previewEl: null,
  historyEl: null,
};

function initImageEditTool() {
  imageEditEls = {
    imageInput: qs("#ie-image-input"),
    imageCanvas: qs("#ie-image-canvas"),
    maskCanvas: qs("#ie-mask-canvas"),
    brushSizeInput: qs("#ie-brush-size"),
    clearMaskBtn: qs("#ie-clear-mask"),
    sendBtn: qs("#ie-send-edit"),
    statusEl: qs("#ie-status"),
    previewEl: qs("#ie-preview"),
    historyEl: qs("#ie-history"),
  };

  if (!imageEditEls.imageCanvas || !imageEditEls.maskCanvas) return;

  const maskCanvas = imageEditEls.maskCanvas;
  const imageCanvas = imageEditEls.imageCanvas;
  const imgCtx = imageCanvas.getContext("2d");
  const maskCtx = maskCanvas.getContext("2d");

  const setStatus = (text) => {
    if (imageEditEls.statusEl) imageEditEls.statusEl.textContent = text || "";
  };

  const resizeCanvases = (width, height) => {
    imageCanvas.width = width;
    imageCanvas.height = height;
    maskCanvas.width = width;
    maskCanvas.height = height;
    maskCtx.fillStyle = "black";
    maskCtx.fillRect(0, 0, width, height);
  };

  const drawImageFromSrc = (src) => {
    const img = new Image();
    img.onload = () => {
      resizeCanvases(img.width, img.height);
      imgCtx.clearRect(0, 0, img.width, img.height);
      imgCtx.drawImage(img, 0, 0);
    };
    img.src = src;
  };

  const drawImageFromFile = (file) => {
    const objectUrl = URL.createObjectURL(file);
    drawImageFromSrc(objectUrl);
  };

  const drawImageFromDataUrl = (dataUrl) => {
    imageEditState.currentImageDataUrl = dataUrl;
    drawImageFromSrc(dataUrl);
  };

  const drawMaskAtPointer = (event) => {
    if (!imageEditState.drawing) return;
    const rect = maskCanvas.getBoundingClientRect();
    const scaleX = maskCanvas.width / rect.width;
    const scaleY = maskCanvas.height / rect.height;
    const x = (event.clientX - rect.left) * scaleX;
    const y = (event.clientY - rect.top) * scaleY;
    const radius = parseInt(imageEditEls.brushSizeInput?.value || "24", 10);

    maskCtx.fillStyle = "white";
    maskCtx.beginPath();
    maskCtx.arc(x, y, radius, 0, Math.PI * 2);
    maskCtx.fill();
  };

  const toBlob = async (canvas) =>
    new Promise((resolve) => {
      canvas.toBlob((blob) => resolve(blob), "image/png");
    });

  const buildMaskCanvas = (invert) => {
    if (!invert) return maskCanvas;
    const temp = document.createElement("canvas");
    temp.width = maskCanvas.width;
    temp.height = maskCanvas.height;
    const ctx = temp.getContext("2d");
    ctx.drawImage(maskCanvas, 0, 0);
    const imgData = ctx.getImageData(0, 0, temp.width, temp.height);
    const data = imgData.data;

    for (let i = 0; i < data.length; i += 4) {
      data[i] = 255 - data[i];
      data[i + 1] = 255 - data[i + 1];
      data[i + 2] = 255 - data[i + 2];
    }

    ctx.putImageData(imgData, 0, 0);
    return temp;
  };

  const toFileFromDataUrl = async (dataUrl, filename) => {
    const res = await fetch(dataUrl);
    const blob = await res.blob();
    return new File([blob], filename, { type: blob.type || "image/png" });
  };

  const renderHistory = () => {
    if (!imageEditEls.historyEl) return;
    imageEditEls.historyEl.innerHTML = "";

    imageEditState.history.forEach((src) => {
      const img = document.createElement("img");
      img.src = src;
      img.addEventListener("click", () => {
        drawImageFromDataUrl(src);
        if (imageEditEls.imageInput) imageEditEls.imageInput.value = "";
      });
      imageEditEls.historyEl.appendChild(img);
    });
  };

  if (imageEditEls.imageInput) {
    imageEditEls.imageInput.addEventListener("change", (e) => {
      const file = e.target.files?.[0];
      if (!file) return;
      imageEditState.currentImageDataUrl = null;
      drawImageFromFile(file);
    });
  }

  maskCanvas.addEventListener("mousedown", (e) => {
    imageEditState.drawing = true;
    drawMaskAtPointer(e);
  });
  maskCanvas.addEventListener("mousemove", drawMaskAtPointer);
  window.addEventListener("mouseup", () => {
    imageEditState.drawing = false;
  });

  if (imageEditEls.clearMaskBtn) {
    imageEditEls.clearMaskBtn.addEventListener("click", () => {
      maskCtx.fillStyle = "black";
      maskCtx.fillRect(0, 0, maskCanvas.width, maskCanvas.height);
    });
  }

  if (imageEditEls.sendBtn) {
    imageEditEls.sendBtn.addEventListener("click", async () => {
      try {
        imageEditEls.sendBtn.disabled = true;
        let imageFile = imageEditEls.imageInput?.files?.[0] || null;

        if (imageEditState.currentImageDataUrl) {
          imageFile = await toFileFromDataUrl(imageEditState.currentImageDataUrl, "history.png");
        }

        if (!imageFile) {
          setStatus("Please upload an image first.");
          return;
        }

        setStatus("Uploading...");
        const invert = !!qs("#ie-invert-mask")?.checked;
        const maskSource = buildMaskCanvas(invert);
        const maskBlob = await toBlob(maskSource);
        if (!maskBlob) {
          setStatus("Mask conversion failed.");
          return;
        }

        const form = new FormData();
        form.append("image[]", imageFile, imageFile.name || "image.png");
        form.append("mask[]", maskBlob, "mask.png");
        form.append("prompt", qs("#ie-prompt")?.value || "");
        form.append("model", qs("#ie-model")?.value || "");
        form.append("response_format", qs("#ie-response-format")?.value || "b64_json");
        form.append("n", qs("#ie-n")?.value || "1");
        form.append("denoise", qs("#ie-denoise")?.value || "0.35");
        form.append("steps", qs("#ie-steps")?.value || "35");
        form.append("cfg_scale", qs("#ie-cfg-scale")?.value || "6");
        form.append("sampler", qs("#ie-sampler")?.value || "dpmpp_2m_sde");
        form.append("scheduler", qs("#ie-scheduler")?.value || "normal");

        const resp = await fetch("/v1/images/edits", { method: "POST", body: form });
        if (!resp.ok) {
          const text = await resp.text();
          setStatus(`Error: ${text}`);
          return;
        }

        const data = await resp.json();
        const entries = data.data || [];
        if (imageEditEls.previewEl) imageEditEls.previewEl.innerHTML = "";

        const newImages = [];
        entries.forEach((entry) => {
          const img = document.createElement("img");
          if (entry.url) {
            img.src = entry.url;
            newImages.push(entry.url);
          } else if (entry.b64_json) {
            img.src = `data:image/png;base64,${entry.b64_json}`;
            newImages.push(img.src);
          } else {
            return;
          }

          if (imageEditEls.previewEl) imageEditEls.previewEl.appendChild(img);
        });

        if (newImages.length > 0) {
          imageEditState.currentImageDataUrl = newImages[0];
          newImages.forEach((src) => imageEditState.history.unshift(src));
          while (imageEditState.history.length > imageEditState.historyLimit) {
            imageEditState.history.pop();
          }
          renderHistory();
        }

        setStatus("Done.");
      } catch (err) {
        setStatus(`Error: ${err}`);
      } finally {
        imageEditEls.sendBtn.disabled = false;
      }
    });
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
  if (!chatEls.container) return null;

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
  if (!chatEls.prompt || !chatEls.model || !chatEls.send) return;

  const prompt = chatEls.prompt.value.trim();
  const model = chatEls.model.value;
  if (!prompt || !model || chatEls.model.disabled) return;

  appendMessage(prompt, "user");
  chatEls.prompt.value = "";
  chatEls.prompt.focus();

  const assistantDiv = appendMessage("", "assistant");
  if (!assistantDiv) return;

  chatEls.send.disabled = true;
  chatEls.model.disabled = true;
  if (chatEls.status) chatEls.status.textContent = "Thinking...";

  let assistantText = "";

  try {
    const formData = new FormData();
    formData.append("model", model);
    formData.append("prompt", prompt);

    const response = await fetch("/chat_stream", {
      method: "POST",
      body: formData,
    });

    if (!response.ok) throw new Error(`HTTP ${response.status}`);

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let done = false;

    while (!done) {
      const result = await reader.read();
      done = result.done;

      if (result.value) {
        const chunk = decoder.decode(result.value, { stream: true });
        assistantText += chunk;

        const isAtBottom = chatEls.container.scrollHeight - chatEls.container.scrollTop - chatEls.container.clientHeight < 20;
        assistantDiv.textContent = assistantText;
        if (isAtBottom) chatEls.container.scrollTop = chatEls.container.scrollHeight;
      }
    }

    if (window.marked) assistantDiv.innerHTML = window.marked.parse(assistantText);
    else assistantDiv.textContent = assistantText;

    if (window.MathJax && window.MathJax.typesetPromise) {
      try {
        await window.MathJax.typesetPromise([assistantDiv]);
      } catch (e) {
        console.warn("MathJax error", e);
      }
    }

    if (chatEls.status) chatEls.status.textContent = "";
  } catch (err) {
    assistantDiv.textContent += `\n[Virhe: ${err}]`;
    if (chatEls.status) chatEls.status.textContent = "Virhe pyynnossa.";
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
      if (logsEls.viewer) logsEls.viewer.textContent = data.error || "Logien luku epaonnistui.";
      if (logsEls.path) logsEls.path.textContent = data.path ? `Path: ${data.path}` : "";
      return;
    }

    if (logsEls.viewer) logsEls.viewer.textContent = (data.lines || []).join("\n");
    if (logsEls.path) logsEls.path.textContent = data.path ? `Path: ${data.path}` : "";
  } catch (err) {
    if (logsEls.viewer) logsEls.viewer.textContent = `Logien luku epaonnistui: ${err}`;
  }
}

// Shared polling scheduler
function syncTabPolling() {
  if (activeTab === "logs") {
    if (!logsTimer) {
      fetchLogs();
      logsTimer = window.setInterval(() => {
        if (activeTab === "logs") fetchLogs();
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
        if (activeTab === "llm") refreshWatchdogPanel();
      }, 4000);
    }
  } else if (llmTimer) {
    window.clearInterval(llmTimer);
    llmTimer = null;
  }
}

// Modal helpers
function initModal() {
  const overlay = qs("#modal-overlay");
  const closeBtn = qs("#close-modal-btn");

  if (closeBtn) closeBtn.addEventListener("click", closeModal);

  if (overlay) {
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) closeModal();
    });
  }
}

function openModal(title, bodyHtml) {
  const overlay = qs("#modal-overlay");
  const modalTitle = qs("#modal-title");
  const modalBody = qs("#modal-body");

  if (modalTitle) modalTitle.textContent = title || "";
  if (modalBody) modalBody.innerHTML = bodyHtml || "";
  if (overlay) overlay.style.display = "flex";
}

function closeModal() {
  const overlay = qs("#modal-overlay");
  if (overlay) overlay.style.display = "none";
}

// App init
function initApp() {
  const mainTabs = qs(".main-tabs");
  if (mainTabs) mainTabs.addEventListener("click", handleMainTabClick);

  initSubtabs();
  initLlmSectionNavigation();
  initModal();
  initPowerButtons();
  initWatchdogPanel();
  initImageControls();
  initImageEditTool();
  initChat();
  initLogs();

  const initial = parseHash();
  activateTab(initial.tab, initial.subtab, { pushHash: false, scrollSubtab: initial.hasSubtab });
  window.addEventListener("hashchange", handleHashChange);

  const canonical = buildHash(activeTab, activeSubtabs[activeTab]);
  if (window.location.hash !== canonical) {
    window.location.hash = canonical;
  }

  refreshStatus();
  statusTimer = window.setInterval(refreshStatus, 5000);
  syncTabPolling();
}

document.addEventListener("DOMContentLoaded", initApp);
