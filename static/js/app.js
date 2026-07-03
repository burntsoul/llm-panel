// DOM helpers
const qs = (sel, root = document) => root.querySelector(sel);
const qsa = (sel, root = document) => Array.from(root.querySelectorAll(sel));

// Tab state + hash routing
const VALID_TABS = ["llm", "image", "links", "chat", "logs", "settings"];
const SUBTAB_OPTIONS = {
  llm: ["overview", "vm-controls", "thermal", "health", "models"],
  image: ["main", "image-edit", "future"],
  links: ["main", "placeholder-a", "placeholder-b"],
  chat: ["main", "placeholder-a", "placeholder-b"],
  logs: ["main", "placeholder-a", "placeholder-b"],
  settings: ["main"],
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
  settings: "main",
};

let statusTimer = null;
let logsTimer = null;
let llmTimer = null;
let previousGpuTemp = null;
let settingsData = null;
let activeSettingsSection = "runtime";
let settingsDirty = false;
let settingsAdvanced = false;
let gpuStatusChart = null;
let gpuStatusWindow = "15m";
let gpuStatusTimer = null;
let gpuStatusVisibleSeries = {
  gpu_temp_c: true,
  gpu_util_percent: true,
  gpu_mem_util_percent: true,
  last_target_xx: true,
  last_applied_xx: true,
};

const GPU_STATUS_WINDOWS = ["5m", "15m", "1h", "6h", "24h"];
const GPU_STATUS_SERIES = {
  gpu_temp_c: { label: "Temperature", color: "#c43c2b", axis: "temp", unit: " C" },
  gpu_util_percent: { label: "GPU load", color: "#2f5d50", axis: "percent", unit: "%" },
  gpu_mem_util_percent: { label: "Memory load", color: "#6d5bd0", axis: "percent", unit: "%" },
  last_target_xx: { label: "Fan target", color: "#b37a16", axis: "fan", unit: " xx" },
  last_applied_xx: { label: "Fan applied", color: "#1f6f9f", axis: "fan", unit: " xx" },
};

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
    let detail = "";
    try {
      const data = await resp.json();
      const error = data && data.detail ? data.detail.error || data.detail : data.error;
      detail = error ? `: ${typeof error === "string" ? error : JSON.stringify(error)}` : "";
    } catch (_) {
      detail = "";
    }
    throw new Error(`HTTP ${resp.status}${detail}`);
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

  const restartServiceBtn = qs("#restart-service-btn");
  if (restartServiceBtn) {
    restartServiceBtn.addEventListener("click", restartService);
  }

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

function setRestartServiceStatus(message, isError = false) {
  const node = qs("#restart-service-status");
  if (!node) return;
  node.textContent = message;
  node.classList.toggle("status-error", !!isError);
  node.classList.toggle("status-ok-text", !isError);
}

async function restartService() {
  const btn = qs("#restart-service-btn");
  if (btn) btn.disabled = true;
  setRestartServiceStatus("Scheduling service reload...");

  try {
    const data = await getJson("/api/service/restart", { method: "POST" });
    if (!data.ok) {
      throw new Error(data.error || data.message || "unknown error");
    }

    const message = data.message || "Service reload scheduled.";
    setRestartServiceStatus(message, false);
    openModal("LLM-agent service", `<p>${message}</p><p>The page will refresh shortly.</p>`);

    window.setTimeout(() => {
      window.location.reload();
    }, 3500);
  } catch (err) {
    setRestartServiceStatus(`Service reload failed: ${err}`, true);
    openModal("Virhe", `<p>Service reload failed: ${err}</p>`);
    if (btn) btn.disabled = false;
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
  const targetTemp = wd && wd.target_temp_c !== undefined ? `${wd.target_temp_c}\u00b0C` : "--";
  const smoothTemp = wd && typeof wd.smoothed_temp_c === "number" ? `${wd.smoothed_temp_c.toFixed(1)}\u00b0C` : "--";
  const projectedTemp = wd && typeof wd.projected_temp_c === "number" ? `${wd.projected_temp_c.toFixed(1)}\u00b0C` : "--";
  const tempRate = wd && typeof wd.temp_rate_c_per_s === "number" ? `${wd.temp_rate_c_per_s.toFixed(2)}\u00b0C/s` : "--";
  const desiredFan = wd && wd.desired_fan_xx !== null && wd.desired_fan_xx !== undefined ? String(wd.desired_fan_xx) : "--";
  const limitedFan = wd && wd.rate_limited_target_xx !== null && wd.rate_limited_target_xx !== undefined ? String(wd.rate_limited_target_xx) : "--";
  const kp = wd && wd.over_target_kp !== undefined ? `${wd.kp || "--"}/${wd.over_target_kp}` : "--";
  const minChange = wd && wd.min_change_interval_seconds !== undefined ? `${wd.min_change_interval_seconds}s` : "--";
  const minDelta = wd && wd.command_min_delta_xx !== undefined ? String(wd.command_min_delta_xx) : "--";
  setText("wd-settings-summary", `PID: poll ${poll}, target ${targetTemp}, smooth/projected ${smoothTemp}/${projectedTemp}, rise ${tempRate}, gain ${kp}, desired/limited ${desiredFan}/${limitedFan}, command ${minChange}/delta ${minDelta}`);
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

// SETTINGS tab logic
function formatSettingValue(field) {
  if (!field) return "";
  if (field.type === "boolean") return field.value ? "true" : "false";
  if (field.unit && field.value !== undefined && field.value !== null && field.value !== "") {
    return `${field.value} ${field.unit}`;
  }
  if (field.value === null || field.value === undefined || field.value === "") return "-";
  return String(field.value);
}

function appendSettingLabelContent(node, field) {
  const text = document.createElement("span");
  text.textContent = field.label || field.key;
  node.appendChild(text);

  if (!field.help) return;
  const help = document.createElement("span");
  help.className = "settings-help";
  help.tabIndex = 0;
  help.setAttribute("role", "button");
  help.setAttribute("aria-label", `${field.label || field.key}: ${field.help}`);
  help.dataset.tooltip = field.help;
  help.textContent = "?";
  node.appendChild(help);
}

function setGpuStatusStatus(message, isError = false) {
  const node = qs("#gpu-status-message");
  if (!node) return;
  node.textContent = message;
  node.classList.toggle("status-error", !!isError);
  node.classList.toggle("status-ok-text", !isError);
}

function formatGpuStatusTime(value) {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function formatGpuStatusValue(value, unit = "") {
  if (value === null || value === undefined || value === "") return "--";
  if (typeof value === "number") return `${Number.isInteger(value) ? value : value.toFixed(1)}${unit}`;
  return `${value}${unit}`;
}

function buildGpuStatusSection() {
  const grid = qs("#settings-field-grid");
  if (!grid) return;
  if (gpuStatusChart) {
    gpuStatusChart.destroy();
    gpuStatusChart = null;
  }
  grid.innerHTML = "";
  grid.classList.add("gpu-status-grid");
  grid.classList.remove("provider-grid");

  const panel = document.createElement("div");
  panel.className = "gpu-status-panel";

  const strip = document.createElement("div");
  strip.className = "gpu-status-strip";
  [
    ["Mode", "gpu-status-mode"],
    ["Temp", "gpu-status-temp"],
    ["GPU load", "gpu-status-util"],
    ["Memory", "gpu-status-mem"],
    ["Target fan", "gpu-status-target"],
    ["Applied fan", "gpu-status-applied"],
  ].forEach(([label, id]) => {
    const item = document.createElement("div");
    item.className = "gpu-status-stat";
    const name = document.createElement("span");
    name.textContent = label;
    const value = document.createElement("strong");
    value.id = id;
    value.textContent = "--";
    item.append(name, value);
    strip.appendChild(item);
  });

  const toolbar = document.createElement("div");
  toolbar.className = "gpu-status-toolbar";
  const toolbarLeft = document.createElement("div");
  toolbarLeft.className = "gpu-status-toolbar-left";
  const toolbarRight = document.createElement("div");
  toolbarRight.className = "gpu-status-toolbar-right";

  const windowGroup = document.createElement("div");
  windowGroup.className = "gpu-status-segmented";
  GPU_STATUS_WINDOWS.forEach((windowId) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.dataset.gpuWindow = windowId;
    btn.textContent = windowId;
    btn.classList.toggle("active", windowId === gpuStatusWindow);
    btn.addEventListener("click", () => {
      gpuStatusWindow = windowId;
      qsa("[data-gpu-window]").forEach((node) => node.classList.toggle("active", node.dataset.gpuWindow === gpuStatusWindow));
      refreshGpuStatusChart();
    });
    windowGroup.appendChild(btn);
  });

  const refreshBtn = document.createElement("button");
  refreshBtn.type = "button";
  refreshBtn.className = "secondary";
  refreshBtn.textContent = "Refresh";
  refreshBtn.addEventListener("click", refreshGpuStatusChart);
  toolbarLeft.appendChild(windowGroup);
  toolbarRight.appendChild(refreshBtn);
  toolbar.append(toolbarLeft, toolbarRight);

  const toggles = document.createElement("div");
  toggles.className = "gpu-status-toggles";
  Object.entries(GPU_STATUS_SERIES).forEach(([key, meta]) => {
    const label = document.createElement("label");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = !!gpuStatusVisibleSeries[key];
    input.addEventListener("change", () => {
      gpuStatusVisibleSeries[key] = input.checked;
      refreshGpuStatusChart(false);
    });
    label.append(input, document.createTextNode(meta.label));
    toggles.appendChild(label);
  });

  const chartWrap = document.createElement("div");
  chartWrap.className = "gpu-status-chart-wrap";
  const canvas = document.createElement("canvas");
  canvas.id = "gpu-status-chart";
  canvas.style.width = "100%";
  canvas.style.height = "100%";
  chartWrap.appendChild(canvas);

  const message = document.createElement("p");
  message.className = "settings-save-status muted";
  message.id = "gpu-status-message";
  message.textContent = "Loading GPU status...";

  const controls = document.createElement("div");
  controls.className = "gpu-status-controls";
  controls.append(toolbar, toggles);

  panel.append(controls, strip, chartWrap, message);
  grid.appendChild(panel);
}

function updateGpuStatusStrip(wd, latestPoint) {
  const source = wd || latestPoint || {};
  setText("gpu-status-mode", source.mode || source.watchdog_mode || "--");
  setText("gpu-status-temp", formatGpuStatusValue(source.gpu_temp_c, " C"));
  setText("gpu-status-util", formatGpuStatusValue(source.gpu_util_percent, "%"));
  setText("gpu-status-mem", formatGpuStatusValue(source.gpu_mem_util_percent, "%"));
  setText("gpu-status-target", formatGpuStatusValue(source.last_target_xx, " xx"));
  setText("gpu-status-applied", formatGpuStatusValue(source.last_applied_xx, " xx"));
}

function renderGpuStatusChart(history) {
  const canvas = qs("#gpu-status-chart");
  if (!canvas || !window.Chart) return;

  const points = history && Array.isArray(history.points) ? history.points : [];
  const labels = points.map((point) => formatGpuStatusTime(point.ts));
  const datasets = Object.entries(GPU_STATUS_SERIES)
    .filter(([key]) => gpuStatusVisibleSeries[key])
    .map(([key, meta]) => ({
      label: meta.label,
      data: points.map((point) => point[key] === null || point[key] === undefined ? null : point[key]),
      borderColor: meta.color,
      backgroundColor: meta.color,
      yAxisID: meta.axis,
      tension: 0.25,
      spanGaps: false,
      pointRadius: 0,
      borderWidth: 2,
    }));

  const config = {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { position: "bottom" },
        tooltip: { callbacks: { title: (items) => items && items[0] ? items[0].label : "" } },
      },
      scales: {
        x: { ticks: { maxTicksLimit: 8 } },
        temp: { type: "linear", position: "left", title: { display: true, text: "C" } },
        percent: { type: "linear", position: "right", min: 0, max: 100, grid: { drawOnChartArea: false }, title: { display: true, text: "%" } },
        fan: { type: "linear", position: "right", min: 0, max: 255, display: true, grid: { drawOnChartArea: false }, title: { display: true, text: "fan xx" } },
      },
    },
  };

  if (gpuStatusChart) {
    gpuStatusChart.data = config.data;
    gpuStatusChart.options = config.options;
    gpuStatusChart.update();
    return;
  }
  gpuStatusChart = new Chart(canvas, config);
}

async function refreshGpuStatusChart(showLoading = true) {
  if (activeTab !== "settings" || activeSettingsSection !== "gpu_status") return;
  if (showLoading) setGpuStatusStatus("Loading GPU status...");

  try {
    const [historyResult, watchdogResult] = await Promise.allSettled([
      getJson(`/api/gpu_status/history?window=${encodeURIComponent(gpuStatusWindow)}`),
      getJson("/api/gpu_watchdog/status"),
    ]);
    if (historyResult.status !== "fulfilled") throw historyResult.reason;

    const history = historyResult.value;
    const wd = watchdogResult.status === "fulfilled" ? watchdogResult.value : null;
    const latestPoint = history.points && history.points.length ? history.points[history.points.length - 1] : null;
    updateGpuStatusStrip(wd, latestPoint);
    renderGpuStatusChart(history);
    const count = history.points ? history.points.length : 0;
    setGpuStatusStatus(`Loaded ${count} points for ${history.window}. Last refresh ${new Date().toLocaleTimeString()}.`);
  } catch (err) {
    setGpuStatusStatus(`GPU status load failed: ${err}`, true);
  }
}

function syncGpuStatusAutoRefresh() {
  const shouldRun = activeTab === "settings" && activeSettingsSection === "gpu_status";
  if (shouldRun && !gpuStatusTimer) {
    refreshGpuStatusChart();
    gpuStatusTimer = window.setInterval(() => refreshGpuStatusChart(false), 10000);
  } else if (!shouldRun && gpuStatusTimer) {
    window.clearInterval(gpuStatusTimer);
    gpuStatusTimer = null;
  }
}

function setOllamaProviderStatus(message, isError = false) {
  const node = qs("#ollama-provider-status");
  if (!node) return;
  node.textContent = message;
  node.classList.toggle("status-error", !!isError);
  node.classList.toggle("status-ok-text", !isError);
}

function setOllamaButtonsDisabled(disabled) {
  qsa("[data-ollama-action]").forEach((btn) => {
    btn.disabled = disabled;
  });
  qsa("[data-ollama-remove]").forEach((btn) => {
    btn.disabled = disabled || btn.dataset.presentNow !== "true";
  });
}

function setOllamaPullProgress(progress) {
  const wrap = qs("#ollama-pull-progress-wrap");
  const bar = qs("#ollama-pull-progress");
  const text = qs("#ollama-pull-progress-text");
  if (!wrap || !bar || !text) return;

  const total = Number(progress && progress.total);
  const completed = Number(progress && progress.completed);
  const hasBytes = Number.isFinite(total) && total > 0 && Number.isFinite(completed);
  const percent = hasBytes ? Math.max(0, Math.min(100, Math.round((completed / total) * 100))) : null;
  const status = progress && progress.status ? progress.status : "Pulling";

  wrap.classList.remove("hidden");
  if (percent === null) {
    bar.removeAttribute("value");
    text.textContent = status;
  } else {
    bar.value = percent;
    text.textContent = `${status} ${percent}%`;
  }
}

function hideOllamaPullProgress() {
  const wrap = qs("#ollama-pull-progress-wrap");
  const bar = qs("#ollama-pull-progress");
  const text = qs("#ollama-pull-progress-text");
  if (wrap) wrap.classList.add("hidden");
  if (bar) {
    bar.value = 0;
    bar.setAttribute("value", "0");
  }
  if (text) text.textContent = "";
}

function renderOllamaProviderRows(rows) {
  const body = qs("#ollama-model-table-body");
  if (!body) return;
  body.innerHTML = "";

  if (!rows || !rows.length) {
    const empty = document.createElement("div");
    empty.className = "provider-model-empty";
    empty.textContent = "No models reported by Ollama.";
    body.appendChild(empty);
    return;
  }

  rows.forEach((row) => {
    const item = document.createElement("div");
    item.className = "provider-model-row";

    const name = document.createElement("strong");
    name.textContent = row.id || "";

    const meta = document.createElement("span");
    let status = "unknown";
    if (row.present_now === true) status = "present";
    else if (row.present_now === false) status = "missing";
    meta.textContent = `${status} / ${row.source || "local"} / ${row.device || "unknown"}`;

    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "danger";
    removeBtn.textContent = "Remove";
    removeBtn.dataset.ollamaRemove = "true";
    removeBtn.dataset.presentNow = row.present_now === true ? "true" : "false";
    removeBtn.disabled = row.present_now !== true;
    removeBtn.addEventListener("click", () => removeOllamaModel(row.id || ""));

    item.append(name, meta, removeBtn);
    body.appendChild(item);
  });
}

async function refreshOllamaProviderModels(showLoading = true) {
  if (showLoading) setOllamaProviderStatus("Refreshing model inventory...");

  try {
    const data = await getJson("/api/models");
    renderOllamaProviderRows(data.models || []);
    setOllamaProviderStatus(`Loaded ${(data.models || []).length} models.`, false);
  } catch (err) {
    setOllamaProviderStatus(`Model refresh failed: ${err}`, true);
  }
}

function parseSseChunk(buffer, onEvent) {
  let cursor = 0;
  while (true) {
    const next = buffer.indexOf("\n\n", cursor);
    if (next === -1) break;

    const rawEvent = buffer.slice(cursor, next);
    cursor = next + 2;
    let eventName = "message";
    const dataLines = [];

    rawEvent.split("\n").forEach((line) => {
      if (line.startsWith("event:")) eventName = line.slice(6).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
    });

    const dataText = dataLines.join("\n");
    let data = dataText;
    if (dataText) {
      try {
        data = JSON.parse(dataText);
      } catch (_) {
        data = dataText;
      }
    }
    onEvent(eventName, data);
  }

  return buffer.slice(cursor);
}

async function pullOllamaModel() {
  const input = qs("#ollama-pull-model");
  const model = input ? input.value.trim() : "";
  if (!model) {
    setOllamaProviderStatus("Enter a model name to pull.", true);
    return;
  }

  setOllamaButtonsDisabled(true);
  hideOllamaPullProgress();
  setOllamaProviderStatus(`Pulling ${model}. This can take a while...`);

  try {
    const resp = await fetch("/api/providers/ollama/models/pull", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model }),
    });

    if (!resp.ok) {
      throw new Error(`HTTP ${resp.status}`);
    }
    if (!resp.body) {
      throw new Error("Streaming response is not available in this browser");
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let doneMessage = `Pulled ${model}.`;

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      buffer = parseSseChunk(buffer, (eventName, data) => {
        if (eventName === "progress") {
          setOllamaPullProgress(data);
          setOllamaProviderStatus(data && data.status ? data.status : `Pulling ${model}...`);
        } else if (eventName === "models") {
          renderOllamaProviderRows(data.models || []);
        } else if (eventName === "done") {
          doneMessage = data && data.message ? data.message : doneMessage;
        } else if (eventName === "error") {
          throw new Error(data && data.error ? data.error : "pull stream failed");
        }
      });
    }

    if (input) input.value = "";
    setOllamaProviderStatus(doneMessage, false);
  } catch (err) {
    setOllamaProviderStatus(`Pull failed: ${err}`, true);
  } finally {
    hideOllamaPullProgress();
    setOllamaButtonsDisabled(false);
  }
}

async function removeOllamaModel(model) {
  const name = String(model || "").trim();
  if (!name) return;
  if (!window.confirm(`Remove Ollama model "${name}"?`)) return;

  setOllamaButtonsDisabled(true);
  setOllamaProviderStatus(`Removing ${name}...`);

  try {
    const data = await getJson(`/api/providers/ollama/models/${encodeURIComponent(name)}`, {
      method: "DELETE",
    });
    renderOllamaProviderRows(data.models || []);
    setOllamaProviderStatus(data.message || `Removed ${name}.`, false);
  } catch (err) {
    setOllamaProviderStatus(`Remove failed: ${err}`, true);
    refreshOllamaProviderModels(false);
  } finally {
    setOllamaButtonsDisabled(false);
  }
}

function buildOllamaProviderSection() {
  const grid = qs("#settings-field-grid");
  if (!grid) return;
  if (gpuStatusChart) {
    gpuStatusChart.destroy();
    gpuStatusChart = null;
  }
  grid.innerHTML = "";
  grid.classList.remove("gpu-status-grid");
  grid.classList.add("provider-grid");

  const panel = document.createElement("div");
  panel.className = "provider-panel";

  const pullBox = document.createElement("div");
  pullBox.className = "provider-pull-box";

  const label = document.createElement("label");
  label.htmlFor = "ollama-pull-model";
  label.textContent = "Model to pull";

  const input = document.createElement("input");
  input.type = "text";
  input.id = "ollama-pull-model";
  input.placeholder = "qwen2.5-coder:7b";
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") pullOllamaModel();
  });

  const pullBtn = document.createElement("button");
  pullBtn.type = "button";
  pullBtn.className = "primary";
  pullBtn.dataset.ollamaAction = "pull";
  pullBtn.textContent = "Pull";
  pullBtn.addEventListener("click", pullOllamaModel);

  const refreshBtn = document.createElement("button");
  refreshBtn.type = "button";
  refreshBtn.className = "secondary";
  refreshBtn.dataset.ollamaAction = "refresh";
  refreshBtn.textContent = "Refresh";
  refreshBtn.addEventListener("click", () => refreshOllamaProviderModels());

  pullBox.append(label, input, pullBtn, refreshBtn);

  const progressWrap = document.createElement("div");
  progressWrap.className = "provider-progress hidden";
  progressWrap.id = "ollama-pull-progress-wrap";
  const progress = document.createElement("progress");
  progress.id = "ollama-pull-progress";
  progress.max = 100;
  progress.value = 0;
  const progressText = document.createElement("span");
  progressText.id = "ollama-pull-progress-text";
  progressWrap.append(progress, progressText);

  const table = document.createElement("div");
  table.className = "provider-model-table";
  const head = document.createElement("div");
  head.className = "provider-model-row provider-model-head";
  ["Model", "State", "Action"].forEach((text) => {
    const node = document.createElement("span");
    node.textContent = text;
    head.appendChild(node);
  });
  const body = document.createElement("div");
  body.id = "ollama-model-table-body";
  table.append(head, body);

  const status = document.createElement("p");
  status.className = "settings-save-status muted";
  status.id = "ollama-provider-status";
  status.textContent = "Loading model inventory...";

  panel.append(pullBox, progressWrap, table, status);
  grid.appendChild(panel);
  refreshOllamaProviderModels();
}

function renderSettingsSection(sectionId) {
  const sections = settingsData && settingsData.sections ? settingsData.sections : {};
  if (!settingsAdvanced && sections[sectionId] && sections[sectionId].advanced) {
    sectionId = "runtime";
  }
  const section = sections[sectionId] || sections.runtime;
  if (!section) return;
  const visibleFields = (section.fields || []).filter((field) => settingsAdvanced || !field.advanced);

  activeSettingsSection = sectionId;
  settingsDirty = false;
  setText("settings-section-title", section.title || "Settings");
  setSettingsSaveStatus(section.editable ? "No changes." : "Read-only branch.");
  updateSettingsSaveButton(section);
  syncSettingsAdvancedVisibility();

  qsa("[data-settings-section]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.settingsSection === sectionId);
  });

  if (section.custom === "gpu_status") {
    buildGpuStatusSection();
    const table = qs("#settings-effective-table");
    if (table) table.innerHTML = "";
    const effectiveWrap = qs("#settings-effective-wrap");
    if (effectiveWrap) effectiveWrap.classList.add("hidden");
    setSettingsSaveStatus("Read-only live status.");
    updateSettingsSaveButton(section);
    syncGpuStatusAutoRefresh();
    return;
  }

  if (section.custom === "provider_ollama") {
    buildOllamaProviderSection();
    const table = qs("#settings-effective-table");
    if (table) table.innerHTML = "";
    const effectiveWrap = qs("#settings-effective-wrap");
    if (effectiveWrap) effectiveWrap.classList.add("hidden");
    setSettingsSaveStatus("Provider model management.");
    updateSettingsSaveButton(section);
    syncGpuStatusAutoRefresh();
    return;
  }

  const grid = qs("#settings-field-grid");
  if (grid) {
    grid.classList.remove("gpu-status-grid");
    grid.classList.remove("provider-grid");
    grid.innerHTML = "";
    if (section.error) {
      const card = document.createElement("div");
      card.className = "settings-field";
      const message = document.createElement("span");
      message.className = "status-error";
      message.textContent = section.error;
      card.appendChild(message);
      grid.appendChild(card);
    }
    visibleFields.forEach((field) => {
      const card = document.createElement("div");
      card.className = "settings-field";
      const editable = !!section.editable && field.editable !== false;

      if (field.type === "boolean") {
        card.classList.add("inline-setting");
        const text = document.createElement("span");
        const label = document.createElement("b");
        appendSettingLabelContent(label, field);
        const source = document.createElement("small");
        source.textContent = field.source || "";
        text.append(label, source);

        const input = document.createElement("input");
        input.type = "checkbox";
        input.checked = !!field.value;
        input.disabled = !editable;
        input.dataset.configKey = field.config_key || "";
        input.addEventListener("change", markSettingsDirty);
        card.append(text, input);
      } else {
        const label = document.createElement("label");
        appendSettingLabelContent(label, field);
        const source = document.createElement("small");
        source.textContent = field.source || "";
        if (editable) {
          const input = document.createElement("input");
          input.type = field.type === "number" ? "number" : "text";
          if (field.type === "number") input.step = "any";
          input.value = field.value === null || field.value === undefined ? "" : String(field.value);
          input.dataset.configKey = field.config_key || "";
          input.addEventListener("input", markSettingsDirty);
          card.append(label, input, source);
        } else {
          const value = document.createElement("code");
          value.className = "settings-value";
          value.textContent = formatSettingValue(field);
          card.append(label, value, source);
        }
      }

      grid.appendChild(card);
    });
  }

  const table = qs("#settings-effective-table");
  if (table) {
    table.innerHTML = "";
    visibleFields.forEach((field) => {
      const row = document.createElement("div");
      row.className = "settings-row";
      const key = document.createElement("span");
      key.textContent = field.key || "";
      const value = document.createElement("code");
      value.textContent = formatSettingValue(field);
      const source = document.createElement("em");
      source.textContent = field.source || "";
      row.append(key, value, source);
      table.appendChild(row);
    });
  }
  syncGpuStatusAutoRefresh();
}

function syncSettingsAdvancedVisibility() {
  qsa("[data-advanced-only='true']").forEach((node) => {
    node.classList.toggle("hidden", !settingsAdvanced);
  });

  const effectiveWrap = qs("#settings-effective-wrap");
  if (effectiveWrap) {
    effectiveWrap.classList.toggle("hidden", !settingsAdvanced);
  }

  const toggle = qs("#settings-advanced-toggle");
  if (toggle) {
    toggle.checked = settingsAdvanced;
  }
}

function setSettingsAdvanced(enabled) {
  settingsAdvanced = !!enabled;
  syncSettingsAdvancedVisibility();

  const currentSection = settingsData && settingsData.sections ? settingsData.sections[activeSettingsSection] : null;
  if (!settingsAdvanced && currentSection && currentSection.advanced) {
    renderSettingsSection("runtime");
  }
}

function setSettingsSaveStatus(message, isError = false) {
  const node = qs("#settings-save-status");
  if (!node) return;
  node.textContent = message;
  node.classList.toggle("status-error", !!isError);
  node.classList.toggle("status-ok-text", !isError && message && message !== "No changes.");
}

function updateSettingsSaveButton(section = null) {
  const saveBtn = qs("#settings-save-btn");
  if (!saveBtn) return;
  const activeSection = section || (settingsData && settingsData.sections ? settingsData.sections[activeSettingsSection] : null);
  saveBtn.disabled = !(activeSection && activeSection.editable && settingsDirty);
}

function markSettingsDirty() {
  settingsDirty = true;
  setSettingsSaveStatus("Unsaved changes.");
  updateSettingsSaveButton();
}

function collectSettingsValues() {
  const values = {};
  qsa("#settings-field-grid [data-config-key]").forEach((input) => {
    const key = input.dataset.configKey;
    if (!key) return;
    if (input.type === "checkbox") values[key] = input.checked;
    else if (input.type === "number") values[key] = input.value === "" ? null : Number(input.value);
    else values[key] = input.value;
  });
  return values;
}

async function saveSettings() {
  const section = settingsData && settingsData.sections ? settingsData.sections[activeSettingsSection] : null;
  if (!section || !section.editable || !section.save_endpoint) return;

  const saveBtn = qs("#settings-save-btn");
  if (saveBtn) saveBtn.disabled = true;
  setSettingsSaveStatus("Saving...");

  try {
    const data = await getJson(section.save_endpoint, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ values: collectSettingsValues() }),
    });

    if (data.section && settingsData.sections) {
      settingsData.sections[activeSettingsSection] = data.section;
    } else {
      settingsData = await getJson("/api/settings/effective");
    }
    renderSettingsSection(activeSettingsSection);
    setSettingsSaveStatus("Saved.", false);
  } catch (err) {
    settingsDirty = true;
    setSettingsSaveStatus(`Save failed: ${err}`, true);
    updateSettingsSaveButton(section);
  }
}

async function loadSettings(sectionId = activeSettingsSection) {
  const grid = qs("#settings-field-grid");
  if (grid && !settingsData) {
    grid.innerHTML = "<div class='settings-field'><span class='muted'>Loading settings...</span></div>";
  }

  try {
    settingsData = await getJson("/api/settings/effective");
    renderSettingsSection(sectionId);
  } catch (err) {
    if (grid) {
      grid.innerHTML = `<div class='settings-field'><span class='status-error'>Settings load failed: ${err}</span></div>`;
    }
  }
}

function initSettings() {
  qsa("[data-settings-section]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const sectionId = btn.dataset.settingsSection || "runtime";
      if (!settingsData) loadSettings(sectionId);
      else renderSettingsSection(sectionId);
    });
  });

  const refreshBtn = qs("#settings-refresh-btn");
  if (refreshBtn) {
    refreshBtn.addEventListener("click", () => loadSettings(activeSettingsSection));
  }

  const advancedToggle = qs("#settings-advanced-toggle");
  if (advancedToggle) {
    advancedToggle.addEventListener("change", () => setSettingsAdvanced(advancedToggle.checked));
  }
  syncSettingsAdvancedVisibility();

  const saveBtn = qs("#settings-save-btn");
  if (saveBtn) {
    saveBtn.addEventListener("click", saveSettings);
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

  if (activeTab === "settings" && !settingsData) {
    loadSettings(activeSettingsSection);
  }
  syncGpuStatusAutoRefresh();
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

  const settingsCogBtn = qs("#settings-cog-btn");
  if (settingsCogBtn) {
    settingsCogBtn.addEventListener("click", () => activateTab("settings", "main", { pushHash: true }));
  }

  initSubtabs();
  initLlmSectionNavigation();
  initModal();
  initPowerButtons();
  initWatchdogPanel();
  initImageControls();
  initImageEditTool();
  initChat();
  initLogs();
  initSettings();

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
