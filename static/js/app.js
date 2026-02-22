// DOM helpers
const qs = (sel, root = document) => root.querySelector(sel);
const qsa = (sel, root = document) => Array.from(root.querySelectorAll(sel));

// Tab state + hash routing
const VALID_TABS = ["llm", "image", "links", "chat", "logs"];

function normalizeHash(hashValue) {
  const value = (hashValue || "").replace(/^#/, "").toLowerCase();
  return VALID_TABS.includes(value) ? value : "llm";
}

function setHash(tabId) {
  if (window.location.hash !== `#${tabId}`) {
    window.location.hash = tabId;
  }
}

// Tab rendering / activation
function activateTab(tabId) {
  const safeTab = normalizeHash(tabId);

  qsa(".main-tab").forEach((tabBtn) => {
    tabBtn.classList.toggle("active", tabBtn.dataset.tab === safeTab);
  });

  qsa(".tab-panel").forEach((panel) => {
    panel.classList.toggle("active", panel.dataset.panel === safeTab);
  });

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

// Subtab placeholders
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

// Status bar placeholder init
function initStatusPlaceholders() {
  qsa(".status-item strong").forEach((node) => {
    if (!node.textContent || !node.textContent.trim()) {
      node.textContent = "--";
    }
  });
}

// App init
function initApp() {
  const mainTabs = qs(".main-tabs");
  if (mainTabs) {
    mainTabs.addEventListener("click", handleMainTabClick);
  }

  initSubtabs();
  initStatusPlaceholders();

  handleHashChange();
  window.addEventListener("hashchange", handleHashChange);
}

document.addEventListener("DOMContentLoaded", initApp);
