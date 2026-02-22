// DOM helpers
const qs = (sel, root = document) => root.querySelector(sel);
const qsa = (sel, root = document) => Array.from(root.querySelectorAll(sel));

// Tab state + hash routing
const VALID_TABS = ["llm", "image", "links", "chat", "logs"];
let activeTab = "llm";
let statusTimer = null;
let logsTimer = null;

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

// Shared status updates
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
    setText("top-watchdog", "--");

    try {
      const gpu = await getJson("/api/gpu_telemetry");
      const util = gpu && typeof gpu.gpu_util_percent === "number" ? `${Math.round(gpu.gpu_util_percent)}%` : "--";
      setText("top-gpu", util);
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
  initImageControls();
  initChat();
  initLogs();

  handleHashChange();
  window.addEventListener("hashchange", handleHashChange);

  refreshStatus();
  statusTimer = window.setInterval(refreshStatus, 10000);
  syncTabPolling();
}

document.addEventListener("DOMContentLoaded", initApp);
