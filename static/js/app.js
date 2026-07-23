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
let ollamaProfiles = [];
let ollamaProviderModels = [];
let llamaCppSettings = null;
let llamaCppProfiles = [];
let llamaCppArtifacts = [];
let llamaCppDownload = null;
let llamaCppDownloadTimer = null;
let llamaCppLogTimer = null;
let llamaCppLogProfileId = null;
let llamaCppLogPinned = true;
let modalCleanup = null;
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

// Model alias state + UI refs
let modelAliases = {};
let aliasEditState = {};
let aliasEls = {
  tableBody: null,
  addAliasModel: null,
  addAliasInput: null,
  addAliasBtn: null,
  saveAliasBtn: null,
  cancelAliasBtn: null,
  status: null,
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
  qsa("[data-ollama-profile-required]").forEach((btn) => {
    btn.disabled = disabled || btn.dataset.profileEnabled !== "true";
  });
  qsa("[data-ollama-backing-required]").forEach((btn) => {
    btn.disabled = disabled || btn.dataset.backingPresent !== "true";
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

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function profileForModel(modelId) {
  return (ollamaProfiles || []).find((profile) => profile.public_model === modelId) || null;
}

function collectProfileFormValues() {
  const parameters = {};
  qsa("#ollama-profile-form [data-profile-param]").forEach((input) => {
    const key = input.dataset.profileParam;
    const value = input.value.trim();
    if (value === "") return;
    if (input.dataset.profileType === "int") parameters[key] = Number.parseInt(value, 10);
    else if (input.dataset.profileType === "float") parameters[key] = Number.parseFloat(value);
    else parameters[key] = value;
  });
  const system = qs("#profile-system")?.value || "";
  return { parameters, system };
}

async function streamOllamaProfileSave(modelId, isEdit) {
  const status = qs("#profile-save-status");
  const saveBtn = qs("#profile-save-btn");
  if (saveBtn) saveBtn.disabled = true;
  if (status) status.textContent = "Saving profile...";

  const values = collectProfileFormValues();
  const url = isEdit
    ? `/api/providers/ollama/profiles/${encodeURIComponent(modelId)}`
    : "/api/providers/ollama/profiles";
  const method = isEdit ? "PUT" : "POST";

  try {
    const resp = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        public_model: modelId,
        parameters: values.parameters,
        system: values.system,
      }),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    if (!resp.body) throw new Error("Streaming response is not available in this browser");

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let doneMessage = "Profile saved.";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      buffer = parseSseChunk(buffer, (eventName, data) => {
        if (eventName === "progress" && status) {
          status.textContent = data && data.status ? data.status : "Creating profile...";
        } else if (eventName === "models") {
          ollamaProfiles = data.profiles || [];
          renderOllamaProviderRows(data.models || []);
        } else if (eventName === "done") {
          doneMessage = data && data.message ? data.message : doneMessage;
        } else if (eventName === "error") {
          throw new Error(data && data.error ? data.error : "profile stream failed");
        }
      });
    }

    if (status) status.textContent = doneMessage;
    setOllamaProviderStatus(doneMessage, false);
    window.setTimeout(closeModal, 700);
  } catch (err) {
    if (status) status.textContent = `Save failed: ${err}`;
    setOllamaProviderStatus(`Profile save failed: ${err}`, true);
  } finally {
    if (saveBtn) saveBtn.disabled = false;
  }
}

function openOllamaProfileEditor(modelId) {
  const profile = profileForModel(modelId);
  const params = profile && profile.parameters ? profile.parameters : {};
  const field = (key, label, type = "int") => `
    <label class="profile-field">
      <span>${label}</span>
      <input type="number" step="${type === "float" ? "0.01" : "1"}" data-profile-param="${key}" data-profile-type="${type}" value="${escapeHtml(params[key] ?? "")}" />
    </label>`;
  const body = `
    <div id="ollama-profile-form" class="profile-form">
      <div class="settings-section-title">${escapeHtml(modelId)}</div>
      <div class="profile-grid">
        ${field("num_ctx", "Context tokens")}
        ${field("num_gpu", "GPU layers")}
        ${field("main_gpu", "Main GPU")}
        ${field("temperature", "Temperature", "float")}
        ${field("top_p", "Top P", "float")}
        ${field("repeat_penalty", "Repeat penalty", "float")}
        ${field("num_predict", "Max tokens")}
        <label class="profile-field">
          <span>Keep alive</span>
          <input type="text" data-profile-param="keep_alive" data-profile-type="text" value="${escapeHtml(params.keep_alive ?? "")}" placeholder="5m" />
        </label>
      </div>
      <label class="profile-system">
        <span>System prompt</span>
        <textarea id="profile-system" rows="5">${escapeHtml(profile && profile.system ? profile.system : "")}</textarea>
      </label>
      <div class="button-row top-gap">
        <button type="button" class="primary" id="profile-save-btn">${profile ? "Update profile" : "Create profile"}</button>
      </div>
      <p class="settings-save-status muted" id="profile-save-status">Profile edits rebuild from the original base model.</p>
    </div>`;
  openModal(profile ? "Edit Ollama profile" : "Create Ollama profile", body);
  const saveBtn = qs("#profile-save-btn");
  if (saveBtn) saveBtn.addEventListener("click", () => streamOllamaProfileSave(modelId, !!profile));
}

function renderOllamaProviderRows(rows) {
  const body = qs("#ollama-model-table-body");
  if (!body) return;
  body.innerHTML = "";
  const safeRows = rows || [];

  if (!safeRows.length) {
    const empty = document.createElement("div");
    empty.className = "provider-model-empty";
    empty.textContent = "No models reported by Ollama.";
    body.appendChild(empty);
    return;
  }

  safeRows.forEach((row) => {
    const modelId = row.raw_model_name || row.id || "";
    const item = document.createElement("div");
    item.className = "provider-model-row";

    const name = document.createElement("strong");
    name.textContent = modelId;
    if (row.alias) {
      name.title = `Exposed as ${row.alias}`;
    }

    const meta = document.createElement("span");
    let status = "unknown";
    if (row.present_now === true) status = "present";
    else if (row.present_now === false) status = "missing";
    meta.textContent = `${status} / ${row.source || "local"} / ${row.device || "unknown"}`;

    const profile = profileForModel(modelId);
    const profileMeta = document.createElement("span");
    profileMeta.textContent = profile ? `profile ${profile.status || "active"}` : "base";

    const actions = document.createElement("div");
    actions.className = "provider-action-row";

    const profileBtn = document.createElement("button");
    profileBtn.type = "button";
    profileBtn.className = "secondary";
    profileBtn.dataset.ollamaAction = "profile";
    profileBtn.textContent = profile ? "Edit profile" : "Create profile";
    profileBtn.addEventListener("click", () => openOllamaProfileEditor(modelId));

    const removeProfileBtn = document.createElement("button");
    removeProfileBtn.type = "button";
    removeProfileBtn.dataset.ollamaAction = "remove-profile";
    removeProfileBtn.dataset.ollamaProfileRequired = "true";
    removeProfileBtn.dataset.profileEnabled = profile ? "true" : "false";
    removeProfileBtn.textContent = "Remove profile";
    removeProfileBtn.disabled = !profile;
    removeProfileBtn.addEventListener("click", () => removeOllamaProfile(modelId));

    const removeBackingBtn = document.createElement("button");
    removeBackingBtn.type = "button";
    removeBackingBtn.dataset.ollamaAction = "remove-backing";
    removeBackingBtn.dataset.ollamaBackingRequired = "true";
    removeBackingBtn.dataset.backingPresent = profile && profile.backing_present === true ? "true" : "false";
    removeBackingBtn.textContent = "Remove backing";
    removeBackingBtn.disabled = !profile || profile.backing_present !== true;
    removeBackingBtn.addEventListener("click", () => removeOllamaProfileBacking(modelId));

    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "danger";
    removeBtn.textContent = "Remove";
    removeBtn.dataset.ollamaRemove = "true";
    removeBtn.dataset.presentNow = row.base_present_now === true ? "true" : "false";
    removeBtn.disabled = row.base_present_now !== true;
    removeBtn.addEventListener("click", () => removeOllamaModel(modelId));

    actions.append(profileBtn, removeProfileBtn, removeBackingBtn, removeBtn);
    item.append(name, meta, profileMeta, actions);
    body.appendChild(item);
  });
}

async function refreshOllamaProviderModels(showLoading = true) {
  if (showLoading) setOllamaProviderStatus("Refreshing model inventory...");

  try {
    const data = await getJson("/api/providers/ollama/models");
    ollamaProfiles = data.profiles || [];
    ollamaProviderModels = data.models || [];
    renderOllamaProviderRows(ollamaProviderModels);
    renderAliasTable();
    setOllamaProviderStatus(`Loaded ${ollamaProviderModels.length} Ollama models.`, false);
  } catch (err) {
    setOllamaProviderStatus(`Model refresh failed: ${err}`, true);
  }
}

async function removeOllamaProfile(modelId) {
  const name = String(modelId || "").trim();
  if (!name) return;
  if (!window.confirm(`Remove llm-agent profile and private backing model for "${name}"?`)) return;
  setOllamaButtonsDisabled(true);
  try {
    const data = await getJson(`/api/providers/ollama/profiles/${encodeURIComponent(name)}`, { method: "DELETE" });
    ollamaProfiles = data.profiles || [];
    renderOllamaProviderRows(data.models || []);
    setOllamaProviderStatus(data.message || `Profile removed for ${name}.`, false);
  } catch (err) {
    setOllamaProviderStatus(`Remove profile failed: ${err}`, true);
  } finally {
    setOllamaButtonsDisabled(false);
  }
}

async function removeOllamaProfileBacking(modelId) {
  const name = String(modelId || "").trim();
  if (!name) return;
  if (!window.confirm(`Remove private Ollama backing model for "${name}"?`)) return;
  setOllamaButtonsDisabled(true);
  try {
    const data = await getJson(`/api/providers/ollama/profiles/${encodeURIComponent(name)}/backing`, { method: "DELETE" });
    ollamaProfiles = data.profiles || [];
    renderOllamaProviderRows(data.models || []);
    setOllamaProviderStatus(data.message || `Backing model removed for ${name}.`, false);
  } catch (err) {
    setOllamaProviderStatus(`Remove backing failed: ${err}`, true);
  } finally {
    setOllamaButtonsDisabled(false);
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

function setLlamaCppStatus(message, isError = false) {
  const node = qs("#llama-cpp-provider-status");
  if (!node) return;
  node.textContent = message || "";
  node.classList.toggle("status-error", !!isError);
}

function llamaCppField(labelText, key, type = "text") {
  const wrap = document.createElement("label");
  wrap.className = "provider-field";
  const label = document.createElement("span");
  label.textContent = labelText;
  const input = document.createElement("input");
  input.type = type;
  input.dataset.llamaSetting = key;
  const value = llamaCppSettings && llamaCppSettings[key];
  if (type === "checkbox") input.checked = !!value;
  else input.value = value == null ? "" : String(value);
  wrap.append(label, input);
  return wrap;
}

function collectLlamaCppSettings() {
  const payload = {};
  qsa("[data-llama-setting]").forEach((input) => {
    const key = input.dataset.llamaSetting;
    payload[key] = input.type === "checkbox" ? input.checked : input.value;
  });
  return payload;
}

function syncLlamaCppSettingsFields() {
  qsa("[data-llama-setting]").forEach((input) => {
    const key = input.dataset.llamaSetting;
    const value = llamaCppSettings && llamaCppSettings[key];
    if (input.type === "checkbox") input.checked = !!value;
    else if (document.activeElement !== input) input.value = value == null ? "" : String(value);
  });
}

async function refreshLlamaCppProvider() {
  try {
    const data = await getJson("/api/providers/llama-cpp/profiles");
    llamaCppSettings = data.settings || {};
    llamaCppProfiles = data.profiles || [];
    syncLlamaCppSettingsFields();
    renderLlamaCppProfiles();
    await refreshLlamaCppDownload(false);
    setLlamaCppStatus(`Loaded ${llamaCppProfiles.length} profiles.`);
  } catch (err) {
    setLlamaCppStatus(`Load failed: ${err}`, true);
  }
}

async function saveLlamaCppSettings() {
  setLlamaCppStatus("Saving llama.cpp settings...");
  try {
    const data = await getJson("/api/providers/llama-cpp/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(collectLlamaCppSettings()),
    });
    llamaCppSettings = data.settings || {};
    setLlamaCppStatus("Settings saved.");
  } catch (err) {
    setLlamaCppStatus(`Save failed: ${err}`, true);
  }
}

async function testLlamaCppSsh() {
  setLlamaCppStatus("Testing SSH...");
  try {
    const data = await getJson("/api/providers/llama-cpp/ssh/test", { method: "POST" });
    setLlamaCppStatus(data.ok ? "SSH OK." : `SSH failed: ${data.message}`, !data.ok);
  } catch (err) {
    setLlamaCppStatus(`SSH test failed: ${err}`, true);
  }
}

async function cleanupLlamaCppRuntime() {
  if (!window.confirm("Stop stale managed llama.cpp server processes?")) return;
  setLlamaCppStatus("Cleaning up llama.cpp runtime...");
  try {
    await getJson("/api/providers/llama-cpp/runtime/cleanup", { method: "POST" });
    await refreshLlamaCppProvider();
    setLlamaCppStatus("Runtime cleanup complete.");
  } catch (err) {
    setLlamaCppStatus(`Runtime cleanup failed: ${err}`, true);
  }
}

function formatBytes(value) {
  const n = Number(value || 0);
  if (!Number.isFinite(n) || n <= 0) return "-";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = n;
  let idx = 0;
  while (size >= 1024 && idx < units.length - 1) {
    size /= 1024;
    idx += 1;
  }
  return `${size.toFixed(idx === 0 ? 0 : 1)} ${units[idx]}`;
}

async function scanLlamaCppArtifacts() {
  setLlamaCppStatus("Scanning GGUF artifacts...");
  try {
    const data = await getJson("/api/providers/llama-cpp/artifacts/scan", { method: "POST" });
    llamaCppArtifacts = data.artifacts || [];
    renderLlamaCppArtifacts();
    setLlamaCppStatus(`Found ${llamaCppArtifacts.length} GGUF files.`);
  } catch (err) {
    setLlamaCppStatus(`Scan failed: ${err}`, true);
  }
}

async function deleteLlamaCppArtifact(path) {
  const target = String(path || "").trim();
  if (!target) return;
  if (!window.confirm(`Delete GGUF "${target}"?`)) return;
  setLlamaCppStatus(`Deleting ${target}...`);
  try {
    const data = await getJson("/api/providers/llama-cpp/artifacts", {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: target }),
    });
    llamaCppArtifacts = data.artifacts || [];
    renderLlamaCppArtifacts();
    setLlamaCppStatus("GGUF deleted.");
  } catch (err) {
    setLlamaCppStatus(`Delete failed: ${err}`, true);
  }
}

function renderLlamaCppDownload() {
  const status = qs("#llama-cpp-download-status");
  const logs = qs("#llama-cpp-download-logs");
  if (!status) return;
  const job = llamaCppDownload;
  if (!job) {
    status.textContent = "No download job.";
    if (logs) logs.textContent = "";
    return;
  }
  status.textContent = `${job.status || "unknown"}: ${job.repo_id || ""} ${job.filename || ""} -> ${job.target_path || ""}`;
}

function isLlamaCppDownloadActive() {
  return !!(llamaCppDownload && ["starting", "running"].includes(llamaCppDownload.status));
}

function syncLlamaCppDownloadPolling() {
  const shouldPoll = activeSettingsSection === "provider_llama_cpp" && isLlamaCppDownloadActive();
  if (shouldPoll && !llamaCppDownloadTimer) {
    llamaCppDownloadTimer = window.setInterval(() => refreshLlamaCppDownload(true), 3000);
  } else if (!shouldPoll && llamaCppDownloadTimer) {
    window.clearInterval(llamaCppDownloadTimer);
    llamaCppDownloadTimer = null;
  }
}

async function refreshLlamaCppDownload(showLogs = false) {
  try {
    const data = await getJson("/api/providers/llama-cpp/downloads/current");
    const previousStatus = llamaCppDownload && llamaCppDownload.status;
    llamaCppDownload = data.download || null;
    renderLlamaCppDownload();
    if (showLogs) await refreshLlamaCppDownloadLogs();
    if (llamaCppDownload && llamaCppDownload.status === "completed" && previousStatus !== "completed") {
      await scanLlamaCppArtifacts();
    }
    syncLlamaCppDownloadPolling();
  } catch (err) {
    setLlamaCppStatus(`Download status failed: ${err}`, true);
    syncLlamaCppDownloadPolling();
  }
}

async function refreshLlamaCppDownloadLogs() {
  const logs = qs("#llama-cpp-download-logs");
  if (!logs) return;
  try {
    const data = await getJson("/api/providers/llama-cpp/downloads/current/logs");
    logs.textContent = data.logs || "";
  } catch (err) {
    logs.textContent = `Log read failed: ${err}`;
  }
}

async function startLlamaCppDownload() {
  const repo = (qs("#llama-cpp-hf-repo") || {}).value || "";
  const filename = (qs("#llama-cpp-hf-file") || {}).value || "";
  const token = (qs("#llama-cpp-hf-token") || {}).value || "";
  if (token.trim()) {
    try {
      const data = await getJson("/api/providers/llama-cpp/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ hf_token: token.trim() }),
      });
      llamaCppSettings = data.settings || llamaCppSettings;
    } catch (err) {
      setLlamaCppStatus(`Token save failed: ${err}`, true);
      return;
    }
  }
  setLlamaCppStatus("Starting GGUF download...");
  try {
    const data = await getJson("/api/providers/llama-cpp/downloads", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ repo_id: repo.trim(), filename: filename.trim() }),
    });
    llamaCppDownload = data.download || null;
    renderLlamaCppDownload();
    await refreshLlamaCppDownloadLogs();
    syncLlamaCppDownloadPolling();
    setLlamaCppStatus("Download started.");
  } catch (err) {
    setLlamaCppStatus(`Download start failed: ${err}`, true);
  }
}

async function cancelLlamaCppDownload() {
  if (!window.confirm("Cancel the active GGUF download? Partial files will remain.")) return;
  try {
    const data = await getJson("/api/providers/llama-cpp/downloads/current/cancel", { method: "POST" });
    llamaCppDownload = data.download || null;
    renderLlamaCppDownload();
    syncLlamaCppDownloadPolling();
    setLlamaCppStatus("Download cancelled.");
  } catch (err) {
    setLlamaCppStatus(`Cancel failed: ${err}`, true);
  }
}

function profileValue(profile, key, fallback = "") {
  if (!profile) return fallback;
  const value = profile[key];
  return value == null ? fallback : value;
}

function defaultLlamaCppCachePath(servedModelId) {
  const served = String(servedModelId || "model").trim();
  const safe = served.replace(/[^A-Za-z0-9_.-]+/g, "-").replace(/^-+|-+$/g, "") || "model";
  const cacheDir = llamaCppSettings && llamaCppSettings.cache_dir ? llamaCppSettings.cache_dir : "/models/llama/.llm-agent-cache";
  return `${cacheDir.replace(/\/+$/g, "")}/${safe}`;
}

function fillLlamaCppProfileCachePath() {
  const cacheEnabled = qs('#llama-cpp-profile-form [data-profile-field="cache_enabled"]');
  const cachePath = qs('#llama-cpp-profile-form [data-profile-field="cache_path"]');
  const servedModel = qs('#llama-cpp-profile-form [data-profile-field="served_model_id"]');
  const status = qs("#llama-cpp-profile-save-status");
  if (!cacheEnabled || !cachePath) return;
  if (!cacheEnabled.checked) {
    if (status) status.textContent = "Profile changes take effect after restart.";
    return;
  }
  if (!String(cachePath.value || "").trim()) {
    cachePath.value = defaultLlamaCppCachePath(servedModel ? servedModel.value : "");
  }
  if (status) status.textContent = `Prompt cache will use slot path ${cachePath.value}`;
}

function openLlamaCppProfileEditor(profile = null, artifact = null) {
  const isEdit = !!profile;
  const settings = llamaCppSettings || {};
  const ggufPath = profileValue(profile, "gguf_path", artifact ? artifact.path : "");
  const hints = {
    served_model_id: "Stable public name clients will request, e.g. qwen3-a3b:256k.",
    gguf_path: "Full path below /models/llama. Use a scanned artifact when possible.",
    port: "llama-server HTTP port. 8081 is the default for the first profile.",
    ctx_size: "Context tokens. Try 4096 for smoke tests, 131072 or 262144 for long-context agents.",
    n_gpu_layers: "GPU offload layers. Empty lets llama.cpp choose; 999 often means as many as possible.",
    main_gpu: "Primary GPU index. Usually 0 for a single GPU.",
    tensor_split: "Comma-separated VRAM split for multi-GPU, e.g. 24,16. Leave empty for one GPU.",
    split_mode: "llama.cpp split mode such as layer or row. Leave empty unless tuning multi-GPU.",
    threads: "CPU decode threads. Often physical cores or slightly below.",
    threads_batch: "CPU prompt-processing threads. Often same as threads or higher.",
    batch_size: "Prompt processing batch size. Common values: 512, 1024, 2048.",
    ubatch_size: "Microbatch size. Common values: 128, 256, 512 when memory is tight.",
    parallel: "Number of parallel slots. Use 1 for single-agent testing.",
    cache_path: "llama-server slot cache directory. If empty and cache is enabled, llm-agent creates one.",
    cache_mode: "Reserved for future slot-cache restore/save behavior; server startup currently uses read-write cache.",
    cache_ram: "Prompt cache RAM budget in MiB. 8192 matches llama-server's common default.",
    cache_reuse: "Minimum chunk size for KV-shift cache reuse. Leave 0 until tuning.",
    ctx_checkpoints: "Context checkpoints per slot. 32 is a good default for finding reusable prefixes.",
    checkpoint_min_step: "Minimum token spacing between checkpoints. 256 is the llama-server default.",
  };
  const checkHints = {
    flash_attn: "Usually worth enabling on supported GPUs/models; disable if llama.cpp errors.",
    cont_batching: "Good default for server mode and multiple requests; safe to keep enabled.",
    cache_enabled: "Enables llama.cpp prompt cache file usage for repeated long prefixes.",
    cache_idle_slots: "Saves idle slots into the prompt cache before they are reused by another request.",
  };
  const fieldHtml = (labelText, key, type, value) => `
    <label class="profile-field">
      <span>${escapeHtml(labelText)}</span>
      <input type="${type}" data-profile-field="${key}" value="${escapeHtml(value == null ? "" : value)}" title="${escapeHtml(hints[key] || "")}" />
      <small>${escapeHtml(hints[key] || "")}</small>
    </label>`;
  const fields = [
    ["Served model ID", "served_model_id", "text", profileValue(profile, "served_model_id", "")],
    ["GGUF path", "gguf_path", "text", ggufPath],
    ["Port", "port", "number", profileValue(profile, "port", settings.default_port || 8081)],
    ["Context", "ctx_size", "number", profileValue(profile, "ctx_size", 262144)],
    ["GPU layers", "n_gpu_layers", "number", profileValue(profile, "n_gpu_layers", "")],
    ["Main GPU", "main_gpu", "number", profileValue(profile, "main_gpu", "")],
    ["Tensor split", "tensor_split", "text", profileValue(profile, "tensor_split", "")],
    ["Split mode", "split_mode", "text", profileValue(profile, "split_mode", "")],
    ["Threads", "threads", "number", profileValue(profile, "threads", "")],
    ["Threads batch", "threads_batch", "number", profileValue(profile, "threads_batch", "")],
    ["Batch size", "batch_size", "number", profileValue(profile, "batch_size", "")],
    ["uBatch size", "ubatch_size", "number", profileValue(profile, "ubatch_size", "")],
    ["Parallel", "parallel", "number", profileValue(profile, "parallel", "")],
    ["Cache path", "cache_path", "text", profileValue(profile, "cache_path", "")],
    ["Cache mode", "cache_mode", "text", profileValue(profile, "cache_mode", "rw")],
    ["Cache RAM MiB", "cache_ram", "number", profileValue(profile, "cache_ram", 8192)],
    ["Cache reuse", "cache_reuse", "number", profileValue(profile, "cache_reuse", 0)],
    ["Ctx checkpoints", "ctx_checkpoints", "number", profileValue(profile, "ctx_checkpoints", 32)],
    ["Checkpoint min step", "checkpoint_min_step", "number", profileValue(profile, "checkpoint_min_step", 256)],
  ];
  const checks = [
    ["Flash attention", "flash_attn", profileValue(profile, "flash_attn", false)],
    ["Continuous batching", "cont_batching", profileValue(profile, "cont_batching", true)],
    ["Prompt cache", "cache_enabled", profileValue(profile, "cache_enabled", false)],
    ["Cache idle slots", "cache_idle_slots", profileValue(profile, "cache_idle_slots", true)],
  ];
  const extraArgs = Array.isArray(profile && profile.extra_args) ? profile.extra_args.join(" ") : "";
  const body = `
    <div id="llama-cpp-profile-form" class="profile-form">
      <div class="profile-grid">
        ${fields.map(([labelText, key, type, value]) => fieldHtml(labelText, key, type, value)).join("")}
      </div>
      ${checks.map(([labelText, key, checked]) => `
        <label class="profile-check-row" title="${escapeHtml(checkHints[key] || "")}">
          <input type="checkbox" data-profile-field="${key}" ${checked ? "checked" : ""} />
          ${escapeHtml(labelText)}
          <small>${escapeHtml(checkHints[key] || "")}</small>
        </label>`).join("")}
      <label class="profile-system">
        <span>Extra args</span>
        <textarea data-profile-field="extra_args" rows="3" title="Additional llama-server flags, split by spaces. Example: --cache-type-k q8_0 --cache-type-v q8_0">${escapeHtml(extraArgs)}</textarea>
        <small>Additional llama-server flags, e.g. --cache-type-k q8_0 --cache-type-v q8_0.</small>
      </label>
      <div class="button-row top-gap">
        <button type="button" class="primary" id="llama-cpp-profile-save-btn">${isEdit ? "Save profile" : "Create profile"}</button>
      </div>
      <p class="settings-save-status muted" id="llama-cpp-profile-save-status">Profile changes take effect after restart.</p>
    </div>`;
  openModal(isEdit ? "Edit llama.cpp profile" : "Create llama.cpp profile", body);
  const cacheEnabled = qs('#llama-cpp-profile-form [data-profile-field="cache_enabled"]');
  if (cacheEnabled) cacheEnabled.addEventListener("change", fillLlamaCppProfileCachePath);
  const servedModel = qs('#llama-cpp-profile-form [data-profile-field="served_model_id"]');
  if (servedModel) servedModel.addEventListener("input", fillLlamaCppProfileCachePath);
  fillLlamaCppProfileCachePath();
  const saveBtn = qs("#llama-cpp-profile-save-btn");
  if (saveBtn) saveBtn.addEventListener("click", () => saveLlamaCppProfile(profile && profile.id));
}

function collectLlamaCppProfileForm() {
  const payload = {};
  qsa("#llama-cpp-profile-form [data-profile-field]").forEach((input) => {
    const key = input.dataset.profileField;
    if (input.type === "checkbox") payload[key] = input.checked;
    else if (key === "extra_args") payload[key] = input.value.trim() ? input.value.trim().split(/\s+/) : [];
    else payload[key] = input.value;
  });
  const cacheEnabled = qs('#llama-cpp-profile-form [data-profile-field="cache_enabled"]');
  const cachePath = qs('#llama-cpp-profile-form [data-profile-field="cache_path"]');
  if (cacheEnabled) payload.cache_enabled = !!cacheEnabled.checked;
  if (cachePath) payload.cache_path = cachePath.value;
  if (payload.cache_enabled && !String(payload.cache_path || "").trim()) {
    payload.cache_path = defaultLlamaCppCachePath(payload.served_model_id);
  }
  return payload;
}

async function saveLlamaCppProfile(profileId = null) {
  const isEdit = !!profileId;
  const status = qs("#llama-cpp-profile-save-status");
  const saveBtn = qs("#llama-cpp-profile-save-btn");
  fillLlamaCppProfileCachePath();
  const payload = collectLlamaCppProfileForm();
  if (status) status.textContent = "Saving profile...";
  if (saveBtn) saveBtn.disabled = true;
  try {
    const data = await getJson(isEdit ? `/api/providers/llama-cpp/profiles/${profileId}` : "/api/providers/llama-cpp/profiles", {
      method: isEdit ? "PUT" : "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    llamaCppProfiles = data.profiles || [];
    renderLlamaCppProfiles();
    closeModal();
    const saved = data.profile || {};
    const cacheState = saved.cache_enabled ? `cache on (${saved.cache_path || "no path"})` : "cache off";
    const cacheRam = saved.cache_ram == null || saved.cache_ram === "" ? "default" : `${saved.cache_ram} MiB`;
    setLlamaCppStatus(`${isEdit ? "Profile saved" : "Profile created"}: ${cacheState}, cache RAM ${cacheRam}.`);
  } catch (err) {
    if (status) status.textContent = `Save failed: ${err}`;
    setLlamaCppStatus(`Profile save failed: ${err}`, true);
  } finally {
    if (saveBtn) saveBtn.disabled = false;
  }
}

async function llamaCppProfileAction(profileId, action) {
  setLlamaCppStatus(`${action} profile...`);
  try {
    const data = await getJson(`/api/providers/llama-cpp/profiles/${profileId}/${action}`, { method: "POST" });
    llamaCppProfiles = data.profiles || [];
    renderLlamaCppProfiles();
    setLlamaCppStatus(`Profile ${action} requested.`);
  } catch (err) {
    setLlamaCppStatus(`${action} failed: ${err}`, true);
  }
}

async function deleteLlamaCppProfile(profileId) {
  if (!window.confirm("Delete this llama.cpp profile?")) return;
  try {
    const data = await getJson(`/api/providers/llama-cpp/profiles/${profileId}`, { method: "DELETE" });
    llamaCppProfiles = data.profiles || [];
    renderLlamaCppProfiles();
    setLlamaCppStatus("Profile deleted.");
  } catch (err) {
    setLlamaCppStatus(`Delete failed: ${err}`, true);
  }
}

function isLlamaCppLogPinned() {
  const output = qs("#llama-cpp-log-output");
  if (!output) return true;
  return output.scrollHeight - output.scrollTop - output.clientHeight <= 48;
}

function setLlamaCppLogPinned(pinned) {
  llamaCppLogPinned = !!pinned;
  const status = qs("#llama-cpp-log-status");
  const jump = qs("#llama-cpp-log-jump-btn");
  if (status) status.textContent = llamaCppLogPinned ? "Live" : "Paused auto-scroll";
  if (jump) jump.classList.toggle("hidden", llamaCppLogPinned);
}

function scrollLlamaCppLogsToLatest() {
  const output = qs("#llama-cpp-log-output");
  if (!output) return;
  output.scrollTop = output.scrollHeight;
  setLlamaCppLogPinned(true);
}

async function refreshLlamaCppLogs(manual = false) {
  const profileId = llamaCppLogProfileId;
  const output = qs("#llama-cpp-log-output");
  const status = qs("#llama-cpp-log-status");
  if (!profileId || !output) return;
  const shouldPin = llamaCppLogPinned || isLlamaCppLogPinned();
  if (manual && status) status.textContent = "Refreshing...";
  try {
    const data = await getJson(`/api/providers/llama-cpp/profiles/${profileId}/logs?lines=500`);
    const scrollTop = output.scrollTop;
    output.textContent = data.logs || "";
    if (shouldPin) {
      scrollLlamaCppLogsToLatest();
    } else {
      output.scrollTop = scrollTop;
      setLlamaCppLogPinned(false);
    }
  } catch (err) {
    if (status) status.textContent = `Refresh failed: ${err}`;
    setLlamaCppStatus(`Log read failed: ${err}`, true);
  }
}

function stopLlamaCppLogPolling() {
  if (llamaCppLogTimer) {
    window.clearInterval(llamaCppLogTimer);
    llamaCppLogTimer = null;
  }
  llamaCppLogProfileId = null;
}

function showLlamaCppLogs(profileId) {
  const body = `
    <div class="llama-cpp-log-viewer">
      <div class="llama-cpp-log-toolbar">
        <span class="llama-cpp-log-status" id="llama-cpp-log-status">Loading...</span>
        <div class="provider-action-row">
          <button type="button" class="secondary" id="llama-cpp-log-refresh-btn">Refresh</button>
          <button type="button" class="secondary hidden" id="llama-cpp-log-jump-btn">Jump to latest</button>
        </div>
      </div>
      <pre class="log-tail llama-cpp-log-output" id="llama-cpp-log-output"></pre>
    </div>`;
  openModal("llama.cpp logs", body);
  llamaCppLogProfileId = profileId;
  setLlamaCppLogPinned(true);
  setModalCleanup(stopLlamaCppLogPolling);

  const output = qs("#llama-cpp-log-output");
  const refresh = qs("#llama-cpp-log-refresh-btn");
  const jump = qs("#llama-cpp-log-jump-btn");
  if (output) output.addEventListener("scroll", () => setLlamaCppLogPinned(isLlamaCppLogPinned()));
  if (refresh) refresh.addEventListener("click", () => refreshLlamaCppLogs(true));
  if (jump) jump.addEventListener("click", scrollLlamaCppLogsToLatest);

  refreshLlamaCppLogs();
  llamaCppLogTimer = window.setInterval(() => refreshLlamaCppLogs(), 2000);
}

function renderLlamaCppArtifacts() {
  const body = qs("#llama-cpp-artifact-body");
  if (!body) return;
  body.innerHTML = "";
  if (!llamaCppArtifacts.length) {
    const empty = document.createElement("div");
    empty.className = "provider-model-empty";
    empty.textContent = "No GGUF files scanned.";
    body.appendChild(empty);
    return;
  }
  llamaCppArtifacts.forEach((artifact) => {
    const row = document.createElement("div");
    row.className = "provider-model-row";
    const name = document.createElement("strong");
    name.textContent = artifact.name || artifact.path;
    const path = document.createElement("span");
    path.textContent = artifact.path;
    const size = document.createElement("span");
    size.textContent = formatBytes(artifact.size);
    const actions = document.createElement("div");
    actions.className = "provider-action-row";
    const create = document.createElement("button");
    create.type = "button";
    create.className = "secondary";
    create.textContent = "Create profile";
    create.addEventListener("click", () => openLlamaCppProfileEditor(null, artifact));
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "danger";
    remove.textContent = "Delete";
    remove.addEventListener("click", () => deleteLlamaCppArtifact(artifact.path));
    actions.append(create, remove);
    row.append(name, path, size, actions);
    body.appendChild(row);
  });
}

function renderLlamaCppProfiles() {
  const body = qs("#llama-cpp-profile-body");
  if (!body) return;
  body.innerHTML = "";
  if (!llamaCppProfiles.length) {
    const empty = document.createElement("div");
    empty.className = "provider-model-empty";
    empty.textContent = "No llama.cpp profiles.";
    body.appendChild(empty);
    return;
  }
  llamaCppProfiles.forEach((profile) => {
    const row = document.createElement("div");
    row.className = "provider-model-row";
    const model = document.createElement("strong");
    model.textContent = profile.served_model_id || profile.id;
    const state = document.createElement("span");
    state.textContent = `${profile.status || "unknown"} :${profile.port || ""}`;
    const summary = document.createElement("span");
    summary.textContent = `ctx ${profile.ctx_size || "-"} cache ${profile.cache_enabled ? "on" : "off"}`;
    const actions = document.createElement("div");
    actions.className = "provider-action-row";
    [
      ["Start", () => llamaCppProfileAction(profile.id, "start")],
      ["Stop", () => llamaCppProfileAction(profile.id, "stop")],
      ["Restart", () => llamaCppProfileAction(profile.id, "restart")],
      ["Edit", () => openLlamaCppProfileEditor(profile)],
      ["Logs", () => showLlamaCppLogs(profile.id)],
      ["Delete", () => deleteLlamaCppProfile(profile.id)],
    ].forEach(([text, handler]) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = text === "Delete" ? "danger" : "secondary";
      btn.textContent = text;
      btn.addEventListener("click", handler);
      actions.appendChild(btn);
    });
    row.append(model, state, summary, actions);
    body.appendChild(row);
  });
}

function buildLlamaCppProviderSection() {
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

  const connection = document.createElement("div");
  connection.className = "provider-pull-box provider-config-box";
  [
    ["SSH enabled", "ssh_enabled", "checkbox"],
    ["SSH host", "ssh_host", "text"],
    ["SSH user", "ssh_user", "text"],
    ["SSH port", "ssh_port", "number"],
    ["SSH key", "ssh_key", "text"],
    ["Strict host key", "ssh_strict_host_key", "checkbox"],
    ["Model dir", "model_dir", "text"],
    ["llama-server", "binary_path", "text"],
    ["Runtime dir", "runtime_dir", "text"],
    ["Cache dir", "cache_dir", "text"],
    ["Default port", "default_port", "number"],
    ["HF token", "hf_token", "text"],
  ].forEach(([label, key, type]) => connection.appendChild(llamaCppField(label, key, type)));

  const saveBtn = document.createElement("button");
  saveBtn.type = "button";
  saveBtn.className = "primary";
  saveBtn.textContent = "Save settings";
  saveBtn.addEventListener("click", saveLlamaCppSettings);
  const testBtn = document.createElement("button");
  testBtn.type = "button";
  testBtn.className = "secondary";
  testBtn.textContent = "Test SSH";
  testBtn.addEventListener("click", testLlamaCppSsh);
  const scanBtn = document.createElement("button");
  scanBtn.type = "button";
  scanBtn.className = "secondary";
  scanBtn.textContent = "Scan GGUF";
  scanBtn.addEventListener("click", scanLlamaCppArtifacts);
  const cleanupBtn = document.createElement("button");
  cleanupBtn.type = "button";
  cleanupBtn.className = "secondary";
  cleanupBtn.textContent = "Cleanup runtime";
  cleanupBtn.addEventListener("click", cleanupLlamaCppRuntime);
  connection.append(saveBtn, testBtn, scanBtn, cleanupBtn);

  const downloadBox = document.createElement("div");
  downloadBox.className = "provider-pull-box provider-config-box";
  const repoLabel = document.createElement("label");
  repoLabel.className = "provider-field";
  repoLabel.innerHTML = "<span>Hugging Face repo</span>";
  const repoInput = document.createElement("input");
  repoInput.type = "text";
  repoInput.id = "llama-cpp-hf-repo";
  repoInput.placeholder = "Qwen/Qwen3.6-35B-A3B-GGUF";
  repoLabel.appendChild(repoInput);
  const fileLabel = document.createElement("label");
  fileLabel.className = "provider-field";
  fileLabel.innerHTML = "<span>GGUF filename</span>";
  const fileInput = document.createElement("input");
  fileInput.type = "text";
  fileInput.id = "llama-cpp-hf-file";
  fileInput.placeholder = "Qwen3.6-35B-A3B-Q4_K_M.gguf";
  fileLabel.appendChild(fileInput);
  const tokenLabel = document.createElement("label");
  tokenLabel.className = "provider-field";
  tokenLabel.innerHTML = "<span>Optional HF token</span>";
  const tokenInput = document.createElement("input");
  tokenInput.type = "password";
  tokenInput.id = "llama-cpp-hf-token";
  tokenInput.placeholder = llamaCppSettings && llamaCppSettings.hf_token_configured ? "Configured" : "";
  tokenLabel.appendChild(tokenInput);
  const startDownloadBtn = document.createElement("button");
  startDownloadBtn.type = "button";
  startDownloadBtn.className = "primary";
  startDownloadBtn.textContent = "Download GGUF";
  startDownloadBtn.addEventListener("click", startLlamaCppDownload);
  const cancelDownloadBtn = document.createElement("button");
  cancelDownloadBtn.type = "button";
  cancelDownloadBtn.className = "secondary";
  cancelDownloadBtn.textContent = "Cancel";
  cancelDownloadBtn.addEventListener("click", cancelLlamaCppDownload);
  const refreshDownloadBtn = document.createElement("button");
  refreshDownloadBtn.type = "button";
  refreshDownloadBtn.className = "secondary";
  refreshDownloadBtn.textContent = "Refresh download";
  refreshDownloadBtn.addEventListener("click", () => refreshLlamaCppDownload(true));
  const downloadStatus = document.createElement("p");
  downloadStatus.className = "settings-save-status muted";
  downloadStatus.id = "llama-cpp-download-status";
  downloadStatus.textContent = "No download job.";
  const downloadLogs = document.createElement("pre");
  downloadLogs.className = "log-tail";
  downloadLogs.id = "llama-cpp-download-logs";
  downloadBox.append(repoLabel, fileLabel, tokenLabel, startDownloadBtn, cancelDownloadBtn, refreshDownloadBtn, downloadStatus, downloadLogs);

  const artifactTable = document.createElement("div");
  artifactTable.className = "provider-model-table";
  const artifactHead = document.createElement("div");
  artifactHead.className = "provider-model-row provider-model-head";
  ["Artifact", "Path", "Size", "Actions"].forEach((text) => {
    const node = document.createElement("span");
    node.textContent = text;
    artifactHead.appendChild(node);
  });
  const artifactBody = document.createElement("div");
  artifactBody.id = "llama-cpp-artifact-body";
  artifactTable.append(artifactHead, artifactBody);

  const profileTable = document.createElement("div");
  profileTable.className = "provider-model-table";
  const profileHead = document.createElement("div");
  profileHead.className = "provider-model-row provider-model-head";
  ["Model", "State", "Runtime", "Actions"].forEach((text) => {
    const node = document.createElement("span");
    node.textContent = text;
    profileHead.appendChild(node);
  });
  const profileBody = document.createElement("div");
  profileBody.id = "llama-cpp-profile-body";
  profileTable.append(profileHead, profileBody);

  const createBtn = document.createElement("button");
  createBtn.type = "button";
  createBtn.className = "secondary";
  createBtn.textContent = "New profile";
  createBtn.addEventListener("click", () => openLlamaCppProfileEditor());

  const status = document.createElement("p");
  status.className = "settings-save-status muted";
  status.id = "llama-cpp-provider-status";
  status.textContent = "Loading llama.cpp provider...";

  panel.append(connection, downloadBox, createBtn, artifactTable, profileTable, status);
  grid.appendChild(panel);
  refreshLlamaCppProvider();
  renderLlamaCppArtifacts();
  renderLlamaCppDownload();
  syncLlamaCppDownloadPolling();
}

// ============================================================================
// Model Alias Management UI
// ============================================================================

function renderAliasTable() {
  const tableBody = aliasEls.tableBody;
  if (!tableBody) return;
  tableBody.innerHTML = "";

  const allAliases = modelAliases;
  const models = getAllModelNamesFromTable();

  if (Object.keys(allAliases).length === 0 && models.length === 0) {
    const empty = document.createElement("div");
    empty.className = "provider-model-empty";
    empty.textContent = "No models to assign aliases to.";
    tableBody.appendChild(empty);
    return;
  }

  const allModelNames = [...new Set([...models, ...Object.keys(allAliases)])];
  allModelNames.forEach((modelName) => {
    const currentAlias = allAliases[modelName] || "";
    const isEditing = !!aliasEditState[modelName];

    const row = document.createElement("div");
    row.className = "provider-model-row";

    const nameSpan = document.createElement("strong");
    nameSpan.textContent = modelName;

    const aliasSpan = document.createElement("span");
    aliasSpan.className = "model-alias-display";
    aliasSpan.textContent = currentAlias || "—";

    const actions = document.createElement("div");
    actions.className = "provider-action-row";

    if (isEditing) {
      const input = document.createElement("input");
      input.type = "text";
      input.placeholder = "Enter alias...";
      input.value = currentAlias;
      input.className = "alias-edit-input";
      input.style.width = "100%";
      input.style.marginBottom = "4px";
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") saveAlias(modelName, input.value);
        if (e.key === "Escape") cancelAliasEdit(modelName);
      });

      const saveBtn = document.createElement("button");
      saveBtn.type = "button";
      saveBtn.className = "primary";
      saveBtn.textContent = "Save";
      saveBtn.addEventListener("click", () => saveAlias(modelName, input.value));

      const cancelBtn = document.createElement("button");
      cancelBtn.type = "button";
      cancelBtn.className = "secondary";
      cancelBtn.textContent = "Cancel";
      cancelBtn.addEventListener("click", () => cancelAliasEdit(modelName));

      actions.append(input, saveBtn, cancelBtn);
      row.append(nameSpan, aliasSpan, actions);
      tableBody.appendChild(row);

      setTimeout(() => input.focus(), 0);
    } else {
      const editBtn = document.createElement("button");
      editBtn.type = "button";
      editBtn.className = "secondary";
      editBtn.textContent = currentAlias ? "Edit alias" : "Set alias";
      editBtn.addEventListener("click", () => startAliasEdit(modelName));

      const removeBtn = document.createElement("button");
      removeBtn.type = "button";
      removeBtn.className = currentAlias ? "danger" : "secondary";
      removeBtn.textContent = currentAlias ? "Remove alias" : "Set alias";
      removeBtn.disabled = !currentAlias;
      removeBtn.addEventListener("click", () => removeAlias(modelName));

      actions.append(editBtn, removeBtn);
      row.append(nameSpan, aliasSpan, actions);
      tableBody.appendChild(row);
    }
  });
}

function getAllModelNamesFromTable() {
  const fromProviderState = (ollamaProviderModels || [])
    .map((row) => row.raw_model_name || row.id || "")
    .map((name) => name.trim())
    .filter(Boolean);
  if (fromProviderState.length) return [...new Set(fromProviderState)];

  const result = [];
  const rows = qsa("#ollama-model-table-body .provider-model-row strong");
  rows.forEach((el) => {
    const name = el.textContent?.trim();
    if (name) result.push(name);
  });
  return result;
}

function startAliasEdit(modelName) {
  aliasEditState[modelName] = true;
  renderAliasTable();
}

function cancelAliasEdit(modelName) {
  delete aliasEditState[modelName];
  renderAliasTable();
}

async function saveAlias(modelName, alias) {
  const aliasClean = (alias || "").trim();
  if (!modelName) return;

  setAliasStatus("Saving alias...");

  try {
    const resp = await fetch(`/api/providers/ollama/aliases/${encodeURIComponent(modelName)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ alias: aliasClean }),
    });

    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}));
      const detail = data?.detail || `HTTP ${resp.status}`;
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }

    const data = await resp.json();
    if (data.alias) modelAliases[data.model_name] = data.alias;
    else delete modelAliases[data.model_name];
    delete aliasEditState[modelName];
    renderAliasTable();
    setAliasStatus(data.message || "Alias saved.", false);
  } catch (err) {
    setAliasStatus(`Save failed: ${err}`, true);
  }
}

async function removeAlias(modelName) {
  if (!window.confirm(`Remove alias for "${modelName}"?`)) return;

  setAliasStatus("Removing alias...");

  try {
    const resp = await fetch(`/api/providers/ollama/aliases/${encodeURIComponent(modelName)}`, {
      method: "DELETE",
    });

    if (!resp.ok) {
      const data = await resp.json().catch(() => ({}));
      const detail = data?.detail || `HTTP ${resp.status}`;
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }

    const data = await resp.json();
    delete modelAliases[modelName];
    delete aliasEditState[modelName];
    renderAliasTable();
    setAliasStatus(data.message || "Alias removed.", false);
  } catch (err) {
    setAliasStatus(`Remove failed: ${err}`, true);
  }
}

async function loadAliases() {
  try {
    const data = await getJson("/api/providers/ollama/aliases");
    modelAliases = data.aliases || {};
  } catch (_) {
    modelAliases = {};
  }
  renderAliasTable();
}

function setAliasStatus(message, isError = false) {
  const node = qs("#alias-provider-status");
  if (!node) return;
  node.textContent = message;
  node.classList.toggle("status-error", !!isError);
  node.classList.toggle("status-ok-text", !isError && message && message !== "Saving alias...");
}

function buildAliasSection() {
  const grid = qs("#settings-field-grid");
  if (!grid) return;
  grid.classList.remove("gpu-status-grid");
  grid.classList.add("provider-grid");

  const panel = document.createElement("div");
  panel.className = "provider-panel";

  const desc = document.createElement("p");
  desc.className = "settings-save-status muted";
  desc.style.marginBottom = "12px";
  desc.textContent = "Assign human-readable aliases to models. Aliases are exposed downstream via the OpenAI-compatible endpoint and shown in UI like Open WebUI.";
  panel.appendChild(desc);

  const table = document.createElement("div");
  table.className = "provider-model-table";
  const head = document.createElement("div");
  head.className = "provider-model-row provider-model-head";
  ["Model", "Alias", "Actions"].forEach((text) => {
    const node = document.createElement("span");
    node.textContent = text;
    head.appendChild(node);
  });
  const body = document.createElement("div");
  body.id = "alias-model-table-body";
  table.append(head, body);

  const status = document.createElement("p");
  status.className = "settings-save-status muted";
  status.id = "alias-provider-status";
  status.textContent = "Loading aliases...";

  panel.append(table, status);
  grid.appendChild(panel);

  // Wire up refs
  aliasEls.tableBody = qs("#alias-model-table-body");

  loadAliases();
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
  ["Model", "State", "Profile", "Actions"].forEach((text) => {
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
  if (sectionId !== "provider_llama_cpp") {
    syncLlamaCppDownloadPolling();
  }
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
    buildAliasSection();
    const table = qs("#settings-effective-table");
    if (table) table.innerHTML = "";
    const effectiveWrap = qs("#settings-effective-wrap");
    if (effectiveWrap) effectiveWrap.classList.add("hidden");
    setSettingsSaveStatus("Provider model management.");
    updateSettingsSaveButton(section);
    syncGpuStatusAutoRefresh();
    return;
  }

  if (section.custom === "provider_llama_cpp") {
    buildLlamaCppProviderSection();
    const table = qs("#settings-effective-table");
    if (table) table.innerHTML = "";
    const effectiveWrap = qs("#settings-effective-wrap");
    if (effectiveWrap) effectiveWrap.classList.add("hidden");
    setSettingsSaveStatus("Provider runtime management.");
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

function runModalCleanup() {
  if (!modalCleanup) return;
  const cleanup = modalCleanup;
  modalCleanup = null;
  try {
    cleanup();
  } catch (_) {
    // Modal cleanup must never block opening or closing another modal.
  }
}

function setModalCleanup(cleanup) {
  modalCleanup = typeof cleanup === "function" ? cleanup : null;
}

function openModal(title, bodyHtml) {
  runModalCleanup();
  const overlay = qs("#modal-overlay");
  const modalTitle = qs("#modal-title");
  const modalBody = qs("#modal-body");

  if (modalTitle) modalTitle.textContent = title || "";
  if (modalBody) modalBody.innerHTML = bodyHtml || "";
  if (overlay) overlay.style.display = "flex";
}

function closeModal() {
  runModalCleanup();
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
