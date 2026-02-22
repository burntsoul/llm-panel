from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import requests
import json
import asyncio
import httpx
import time
import base64
import logging

from config import settings
from lo100 import get_lo100_health_and_temp
from proxmox import get_vm_status, start_vm, shutdown_vm, stop_vm
from state import get_maintenance_mode, toggle_maintenance_mode, set_maintenance_mode
from llm_server import (
    llm_server_up,
    is_llm_server_busy,
    ensure_llm_running,
    ensure_llm_running_and_ready,
    idle_shutdown_loop,
    cpu_activity_poller,
    touch_activity,
)
from models import (
    get_model_display_entries,
    get_models_openai_format,
    get_model_table_status,
    get_embedding_models_openai_format,
    get_cached_embeddings,
    cache_embeddings,
)
from lease_api import router as lease_api_router
from comfyui_service import (
    generate_images as comfyui_generate_images,
    generate_image_edits as comfyui_generate_image_edits,
    comfyui_idle_shutdown_loop,
    ensure_comfyui_ready,
    comfyui_up,
    get_comfyui_last_activity,
    get_comfyui_last_error,
)
from gpu_telemetry import get_gpu_telemetry
from gpu_watchdog import GPUWatchdogService, parse_watchdog_control_payload
from ilo_fan import set_ilo_fan_min, get_last_fan_command_result
from logging_setup import configure_logging

logger = logging.getLogger("llm-agent")

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Include lease + proxy API
app.include_router(lease_api_router)


@app.on_event("startup")
async def _startup():
    configure_logging(settings.LOG_FILE, settings.LOG_LEVEL)
    # Käynnistä idle-shutdown -looppi taustalle
    asyncio.create_task(idle_shutdown_loop())
    # Käynnistä CPU-aktiviteetin polleri
    asyncio.create_task(cpu_activity_poller())
    # Käynnistä ComfyUI idle-shutdown looppi
    asyncio.create_task(comfyui_idle_shutdown_loop())
    app.state.gpu_watchdog = GPUWatchdogService()
    await app.state.gpu_watchdog.start()


@app.on_event("shutdown")
async def _shutdown():
    watchdog = getattr(app.state, "gpu_watchdog", None)
    if watchdog is not None:
        await watchdog.stop()

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    up = llm_server_up()
    try:
        llm_vm = get_vm_status(settings.LLM_VM_ID)
    except Exception as e:
        llm_vm = f"ERROR: {e}"
    try:
        win_vm = get_vm_status(settings.WINDOWS_VM_ID)
    except Exception as e:
        win_vm = f"ERROR: {e}"

    maintenance = get_maintenance_mode()
    entries = get_model_display_entries()

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "llm_up": up,
            "llm_vm": llm_vm,
            "win_vm": win_vm,
            "maintenance": maintenance,
            "model_entries": entries,
            "llm_vm_id": settings.LLM_VM_ID,
            "windows_vm_id": settings.WINDOWS_VM_ID,
        },
    )


@app.get("/legacy", response_class=HTMLResponse)
def legacy_index():
    up = llm_server_up()
    # Proxmox VM statukset
    try:
        llm_vm = get_vm_status(settings.LLM_VM_ID)
    except Exception as e:
        llm_vm = f"ERROR: {e}"
    try:
        win_vm = get_vm_status(settings.WINDOWS_VM_ID)
    except Exception as e:
        win_vm = f"ERROR: {e}"

    maintenance = get_maintenance_mode()
    entries = get_model_display_entries()
    html_models = "".join(
        f'<option value="{e["id"]}">{e["label"]}</option>' for e in entries
    )

    if not html_models:
        html_models = '<option disabled>Ei malleja (ei konfiguroitu)</option>'

    html = f"""
    <html>
    <head>
      <title>LLM-ohjauspaneeli</title>
      <link rel="preconnect" href="https://fonts.googleapis.com">
      <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
      <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&family=Space+Grotesk:wght@400;600;700&display=swap" rel="stylesheet">
      <style>
        :root {{
          --bg-1: #f6f1e9;
          --bg-2: #efe8dd;
          --ink: #1a1a1a;
          --muted: #6b5e54;
          --card: #fffaf2;
          --border: #e5dccf;
          --accent: #d77a3b;
          --accent-2: #2f5d50;
          --ok: #1f8a5b;
          --bad: #c43c2b;
          --shadow: 0 12px 30px rgba(33, 22, 11, 0.12);
          --mono: "JetBrains Mono", "SFMono-Regular", Menlo, monospace;
          --display: "Space Grotesk", "Segoe UI", sans-serif;
        }}
        * {{
          box-sizing: border-box;
        }}
        body {{
          font-family: var(--display);
          color: var(--ink);
          margin: 0;
          background: radial-gradient(circle at top left, #f9efe2 0%, transparent 55%),
            radial-gradient(circle at 80% 20%, #f4d9c4 0%, transparent 55%),
            linear-gradient(135deg, var(--bg-1), var(--bg-2));
        }}
        .page {{
          max-width: 1100px;
          margin: 0 auto;
          padding: 2rem 1.5rem 3rem;
        }}
        h1, h2 {{
          margin-top: 0;
          font-weight: 700;
          letter-spacing: -0.02em;
        }}
        h1 {{
          font-size: 2rem;
        }}
        .panel {{
          background: var(--card);
          border: 1px solid var(--border);
          border-radius: 18px;
          padding: 1.4rem 1.6rem;
          box-shadow: var(--shadow);
        }}
        .panel + .panel {{
          margin-top: 1.2rem;
        }}
        .grid {{
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
          gap: 1.2rem;
          margin-bottom: 1.2rem;
        }}
        .label {{
          font-size: 0.85rem;
          text-transform: uppercase;
          letter-spacing: 0.08em;
          color: var(--muted);
          font-weight: 600;
        }}
        .status-chip {{
          display: inline-flex;
          align-items: center;
          gap: 0.35rem;
          padding: 0.2rem 0.6rem;
          border-radius: 999px;
          font-size: 0.85rem;
          font-weight: 600;
          border: 1px solid transparent;
        }}
        .status-ok {{
          color: var(--ok);
          border-color: rgba(31, 138, 91, 0.25);
          background: rgba(31, 138, 91, 0.08);
        }}
        .status-bad {{
          color: var(--bad);
          border-color: rgba(196, 60, 43, 0.25);
          background: rgba(196, 60, 43, 0.08);
        }}
        .muted {{
          color: var(--muted);
        }}
        .power-buttons button {{
          margin-right: 0.5rem;
          margin-bottom: 0.4rem;
        }}
        #chat-container {{
          max-height: 55vh;
          overflow-y: auto;
          border-radius: 14px;
          padding: 0.85rem;
          background: #fff7ed;
          border: 1px solid #eadfce;
          margin-bottom: 0.75rem;
          display: flex;
          flex-direction: column;
          gap: 0.4rem;
        }}
        .msg {{
          padding: 0.6rem 0.85rem;
          border-radius: 12px;
          white-space: pre-wrap;
          max-width: 85%;
          line-height: 1.4;
        }}
        .msg-user {{
          background: #f6d7b0;
          align-self: flex-end;
        }}
        .msg-assistant {{
          background: #ede6db;
          align-self: flex-start;
        }}
        .msg-system {{
          font-size: 0.85rem;
          color: var(--muted);
          align-self: center;
        }}
        #input-row {{
          display: flex;
          flex-direction: column;
          gap: 0.5rem;
        }}
        #prompt {{
          width: 100%;
          min-height: 90px;
          resize: vertical;
          font-family: var(--display);
          padding: 0.6rem;
          border-radius: 12px;
          border: 1px solid var(--border);
          background: #fffdf8;
        }}
        #status-line {{
          font-size: 0.85rem;
          color: var(--muted);
          min-height: 1.2em;
        }}
        #model-select {{
          margin-bottom: 0.25rem;
        }}
        button {{
          padding: 0.45rem 0.9rem;
          border-radius: 10px;
          border: 1px solid #d2c4b3;
          background: #f3e8db;
          cursor: pointer;
          font-family: var(--display);
          font-weight: 600;
          color: #2a1c12;
        }}
        button.primary {{
          background: var(--accent);
          color: #fff6ec;
          border-color: #c0682d;
        }}
        button.secondary {{
          background: #d5e6de;
          border-color: #b0cfc2;
        }}
        button:disabled {{
          opacity: 0.6;
          cursor: default;
        }}
        .log-box {{
          font-family: var(--mono);
          background: #201f1d;
          color: #f4f0ea;
          padding: 0.8rem;
          border-radius: 12px;
          height: 240px;
          overflow-y: auto;
          white-space: pre-wrap;
          border: 1px solid #3b3025;
        }}
        .log-controls {{
          display: flex;
          flex-wrap: wrap;
          gap: 0.6rem;
          align-items: center;
          margin-bottom: 0.8rem;
        }}
        .log-controls select,
        .log-controls input {{
          padding: 0.35rem 0.6rem;
          border-radius: 8px;
          border: 1px solid var(--border);
          background: #fffdf8;
          font-family: var(--mono);
        }}
        .modal-overlay {{
          position: fixed;
          inset: 0;
          background: rgba(15, 23, 42, 0.45);
          display: none;
          align-items: center;
          justify-content: center;
          z-index: 1000;
        }}
        .modal-card {{
          background: #ffffff;
          border-radius: 12px;
          padding: 1rem 1.25rem;
          max-width: 900px;
          width: 100%;
          max-height: 80vh;
          overflow-y: auto;
          box-shadow: 0 10px 25px rgba(0, 0, 0, 0.25);
        }}
        .modal-header {{
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 0.5rem;
        }}
        .modal-close {{
          border: none;
          background: transparent;
          font-size: 1.2rem;
          cursor: pointer;
        }}
        .link-button {{
          border: none;
          background: none;
          padding: 0;
          margin: 0;
          color: var(--accent-2);
          cursor: pointer;
          font: inherit;
          font-weight: 600;
        }}
        .link-button:hover {{
          text-decoration: underline;
        }}
        @media (max-width: 720px) {{
          .page {{
            padding: 1.5rem 1rem 2rem;
          }}
          h1 {{
            font-size: 1.6rem;
          }}
        }}
      </style>

      <!-- Markdown-renderöinti -->
      <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>

      <!-- MathJax LaTeX-kaavoille -->
      <script>
        window.MathJax = {{
          tex: {{ inlineMath: [['$', '$'], ['\\\\(', '\\\\)']] }},
          svg: {{ fontCache: 'global' }}
        }};
      </script>
      <script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-svg-full.js" async></script>


    </head>
    <body>
      <div class="page">
        <div class="grid">
          <div class="panel">
            <div class="label">LLM Control</div>
            <h1>LLM-palvelin</h1>
            <p>
              <button class="link-button" type="button" onclick="openModelsModal()">
                Näytä mallilista ja tila
              </button>
            </p>
            <p>
              <a class="link-button" href="/tools/image-edit" target="_blank">
                Avaa Image Edit -työkalu
              </a>
            </p>
            <p>LLM-VM tila: <b id="llm-vm-status">{llm_vm}</b></p>
            <p>Windows-VM tila: <b id="win-vm-status">{win_vm}</b></p>
            <p>Huoltotila: <b id="maintenance-status">{'ON' if maintenance else 'OFF'}</b></p>
            <p>LLM API:
              <span id="llm-api-status" class="status-chip { 'status-ok' if up else 'status-bad' }">
                {'UP' if up else 'DOWN'}
              </span>
            </p>
            <p>System health:
              <span id="system-health" class="status-chip status-bad">
                tuntematon
              </span>
            </p>
            <p>CPU0 lämpötila:
              <span id="cpu-temp">-</span>
            </p>

            <div class="power-buttons">
              <div style="margin-bottom:0.6rem;">
                <b>LLM-VM ({settings.LLM_VM_ID})</b><br/>
                <button type="button" onclick="sendPower('llm_on')">Start</button>
                <button type="button" onclick="sendPower('llm_shutdown')">Shutdown</button>
                <button type="button" onclick="sendPower('llm_stop')">Force stop</button>
              </div>

              <div style="margin-bottom:0.6rem;">
                <b>Windows-VM ({settings.WINDOWS_VM_ID})</b><br/>
                <button type="button" onclick="sendPower('win_on')">Start</button>
                <button type="button" onclick="sendPower('win_shutdown')">Shutdown</button>
                <button type="button" onclick="sendPower('win_stop')">Force stop</button>
              </div>

              <div>
                <b>Huolto</b><br/>
                <button type="button" onclick="sendPower('maintenance_toggle')">Toggle huoltotila</button>
              </div>
            </div>
          </div>

          <div class="panel">
            <div class="label">Image Engine</div>
            <h2>ComfyUI</h2>
            <p>ComfyUI API:
              <span id="comfyui-status" class="status-chip status-bad">DOWN</span>
            </p>
            <p>Viimeisin aktiviteetti:
              <span id="comfyui-last-activity" class="muted">-</span>
            </p>
            <p>Viimeisin virhe:
              <span id="comfyui-last-error" class="muted">-</span>
            </p>
            <div style="margin-top:0.6rem;">
              <button type="button" class="secondary" onclick="wakeComfyUI()">Start ComfyUI</button>
              <button type="button" onclick="fetchLogs()">Refresh logs</button>
            </div>
            <p class="muted" style="margin-top:0.6rem;">
              ComfyUI käynnistetään on-demand ja sammutetaan idlen jälkeen.
            </p>
          </div>
        </div>

        <div class="panel">
          <h2>Chat</h2>
          <div id="chat-container"></div>

          <div id="input-row">
            <div id="model-select">
              <label>Malli:</label>
              <select id="model">
                {html_models}
              </select>
            </div>
            <textarea id="prompt" placeholder="Kirjoita viesti ja paina Lähetä..."></textarea>
            <div>
              <button id="send-btn" class="primary" onclick="sendMessage()">Lähetä</button>
            </div>
            <div id="status-line"></div>
          </div>
        </div>

        <div class="panel">
          <div class="label">Logs</div>
          <h2>LLM-agent logit</h2>
          <div class="log-controls">
            <label for="log-lines" class="muted">Rivejä:</label>
            <select id="log-lines">
              <option value="100">100</option>
              <option value="200" selected>200</option>
              <option value="500">500</option>
              <option value="1000">1000</option>
            </select>
            <button type="button" onclick="fetchLogs()">Hae logit</button>
            <span id="log-path" class="muted"></span>
          </div>
          <div id="log-viewer" class="log-box">Logit eivät ole vielä ladattu.</div>
        </div>
      </div>

      <div id="modal-overlay" class="modal-overlay">
        <div class="modal-card">
          <div class="modal-header">
            <h2 id="modal-title" style="margin:0;font-size:1.1rem;"></h2>
            <button class="modal-close" onclick="closeModal()">×</button>
          </div>
          <div id="modal-body"></div>
        </div>
      </div>


      <script>
        const chatContainer = document.getElementById('chat-container');
        const promptInput = document.getElementById('prompt');
        const sendBtn = document.getElementById('send-btn');
        const statusLine = document.getElementById('status-line');
        const modelSelect = document.getElementById('model');
        const modalOverlay = document.getElementById('modal-overlay');
        const modalTitle = document.getElementById('modal-title');
        const modalBody = document.getElementById('modal-body');
        const logViewer = document.getElementById('log-viewer');
        const logLines = document.getElementById('log-lines');
        const logPath = document.getElementById('log-path');

        function openModal(title, bodyHtml) {{
          if (modalTitle) modalTitle.textContent = title || '';
          if (modalBody) modalBody.innerHTML = bodyHtml || '';
          if (modalOverlay) modalOverlay.style.display = 'flex';
        }}
        function closeModal() {{
          if (modalOverlay) modalOverlay.style.display = 'none';
        }}

        function appendMessage(text, role) {{
          const div = document.createElement('div');
          div.classList.add('msg');
          if (role === 'user') div.classList.add('msg-user');
          if (role === 'assistant') div.classList.add('msg-assistant');
          if (role === 'system') div.classList.add('msg-system');
          div.textContent = text;
          chatContainer.appendChild(div);
          chatContainer.scrollTop = chatContainer.scrollHeight;
          return div;
        }}

        function formatIsoTime(value) {{
          if (!value) return '-';
          try {{
            const date = new Date(value);
            if (Number.isNaN(date.getTime())) return value;
            return date.toLocaleString();
          }} catch (e) {{
            return value;
          }}
        }}

        async function refreshStatus() {{
          try {{
            const response = await fetch('/api/status');
            if (!response.ok) {{
              throw new Error('HTTP ' + response.status);
            }}
            const data = await response.json();
            const llmVmEl = document.getElementById('llm-vm-status');
            const winVmEl = document.getElementById('win-vm-status');
            const maintEl = document.getElementById('maintenance-status');
            const llmEl = document.getElementById('llm-api-status');
            const healthEl = document.getElementById('system-health');
            const cpuTempEl = document.getElementById('cpu-temp');
            const comfyEl = document.getElementById('comfyui-status');
            const comfyLastEl = document.getElementById('comfyui-last-activity');
            const comfyErrEl = document.getElementById('comfyui-last-error');

            if (llmVmEl && data.llm_vm) {{
              llmVmEl.textContent = data.llm_vm;
            }}
            if (winVmEl && data.windows_vm) {{
              winVmEl.textContent = data.windows_vm;
            }}
            if (maintEl && (data.maintenance_mode !== undefined)) {{
              maintEl.textContent = data.maintenance_mode ? 'ON' : 'OFF';
            }}

            if (llmEl) {{
              llmEl.textContent = data.llm_up ? 'UP' : 'DOWN';
              llmEl.classList.remove('status-ok', 'status-bad');
              llmEl.classList.add(data.llm_up ? 'status-ok' : 'status-bad');
            }}

            if (healthEl) {{
              const h = data.system_health;
              if (h) {{
                healthEl.textContent = h;
                healthEl.classList.remove('status-ok', 'status-bad');
                const ok =
                  typeof h === 'string'
                    ? h.toLowerCase().startsWith('ok')
                    : false;
                healthEl.classList.add(ok ? 'status-ok' : 'status-bad');
              }} else {{
                healthEl.textContent = 'tuntematon';
                healthEl.classList.remove('status-ok', 'status-bad');
              }}
            }}

            if (cpuTempEl) {{
              cpuTempEl.textContent = data.cpu_temp || '-';
            }}

            if (comfyEl) {{
              const up = !!data.comfyui_up;
              comfyEl.textContent = up ? 'UP' : 'DOWN';
              comfyEl.classList.remove('status-ok', 'status-bad');
              comfyEl.classList.add(up ? 'status-ok' : 'status-bad');
            }}

            if (comfyLastEl) {{
              comfyLastEl.textContent = formatIsoTime(data.comfyui_last_activity);
            }}

            if (comfyErrEl) {{
              comfyErrEl.textContent = data.comfyui_last_error || '-';
            }}
          }} catch (e) {{
            console.warn('Status-päivitys epäonnistui:', e);
          }}
        }}

        async function openModelsModal() {{
          try {{
            const resp = await fetch('/api/models');
            if (!resp.ok) {{
              throw new Error('HTTP ' + resp.status);
            }}
            const data = await resp.json();
            const rows = data.models || [];

            let html = `
              <table style="border-collapse:collapse;width:100%;">
                <thead>
                  <tr>
                    <th style="text-align:left;padding:4px 8px;border-bottom:1px solid #e5e7eb;">Model ID</th>
                    <th style="text-align:left;padding:4px 8px;border-bottom:1px solid #e5e7eb;">Source</th>
                    <th style="text-align:left;padding:4px 8px;border-bottom:1px solid #e5e7eb;">Device</th>
                    <th style="text-align:left;padding:4px 8px;border-bottom:1px solid #e5e7eb;">Tilanne nyt</th>
                    <th style="text-align:left;padding:4px 8px;border-bottom:1px solid #e5e7eb;">Label</th>
                  </tr>
                </thead>
                <tbody>
            `;

            for (const r of rows) {{
              let status;
              if (r.present_now === true) status = '✅ Ollamassa';
              else if (r.present_now === false) status = '❌ Ei Ollamassa';
              else status = '❓ Tuntematon';

              html += `
                <tr>
                  <td style="padding:4px 8px;border-bottom:1px solid #f3f4f6;">${{r.id}}</td>
                  <td style="padding:4px 8px;border-bottom:1px solid #f3f4f6;">${{r.source}}</td>
                  <td style="padding:4px 8px;border-bottom:1px solid #f3f4f6;">${{r.device}}</td>
                  <td style="padding:4px 8px;border-bottom:1px solid #f3f4f6;">${{status}}</td>
                  <td style="padding:4px 8px;border-bottom:1px solid #f3f4f6;">${{r.label}}</td>
                </tr>
              `;
            }}

            html += `
                </tbody>
              </table>
            `;

            openModal('Mallit ja tila', html);
          }} catch (err) {{
            openModal('Virhe', `<p>Mallilistan haku epäonnistui: ${{err}}</p>`);
          }}
        }}

        async function sendPower(action) {{
          try {{
            openModal('Virta-komento', `<p>Lähetetään komentoa <b>${{action}}</b>...</p>`);

            const formData = new FormData();
            formData.append('action', action);

            const resp = await fetch('/power_json', {{
              method: 'POST',
              body: formData
            }});

            if (!resp.ok) {{
              throw new Error('HTTP ' + resp.status);
            }}

            const data = await resp.json();
            const msg = data.message || '(ei viestiä)';
            const powerNow = data.power || 'tuntematon';
            const ok = data.ok === undefined ? true : !!data.ok;

            let bodyHtml = `
              <p>${{msg}}</p>
              <p>Nykyinen virran tila: <b>${{powerNow}}</b></p>
            `;

            if (!ok) {{
              bodyHtml += `<p style="color:#dc2626;font-size:0.9rem;">Komento ei ehkä toteutunut kokonaan.</p>`;
            }}

            openModal('Virta-komento', bodyHtml);

            // Päivitä etusivun status
            refreshStatus();
          }} catch (err) {{
            openModal('Virhe', `<p>Virta-komento epäonnistui: ${{err}}</p>`);
          }}
        }}

        async function wakeComfyUI() {{
          try {{
            const resp = await fetch('/api/comfyui_wake', {{ method: 'POST' }});
            const data = await resp.json();
            if (!data.ok) {{
              openModal('ComfyUI', `<p>ComfyUI käynnistys epäonnistui: ${{data.error || 'tuntematon virhe'}}</p>`);
            }} else {{
              openModal('ComfyUI', `<p>ComfyUI käynnistys OK.</p>`);
            }}
            refreshStatus();
          }} catch (err) {{
            openModal('Virhe', `<p>ComfyUI käynnistys epäonnistui: ${{err}}</p>`);
          }}
        }}

        async function fetchLogs() {{
          try {{
            const lineCount = logLines ? logLines.value : 200;
            const resp = await fetch(`/api/logs?lines=${{lineCount}}`);
            const data = await resp.json();
            if (!data.ok) {{
              if (logViewer) logViewer.textContent = data.error || 'Logien luku epäonnistui.';
              if (logPath) logPath.textContent = data.path ? `Path: ${{data.path}}` : '';
              return;
            }}
            if (logViewer) logViewer.textContent = (data.lines || []).join('\\n');
            if (logPath) logPath.textContent = data.path ? `Path: ${{data.path}}` : '';
          }} catch (err) {{
            if (logViewer) logViewer.textContent = `Logien luku epäonnistui: ${{err}}`;
          }}
        }}


        // Päivitä status heti ja sitten 10 s välein
        window.addEventListener('load', () => {{
          refreshStatus();
          fetchLogs();
          setInterval(refreshStatus, 10000);
        }});


        async function sendMessage() {{
          const prompt = promptInput.value.trim();
          const model = modelSelect.value;
          if (!prompt || !modelSelect.value || modelSelect.disabled) return;

          // oma viesti kuplaan
          appendMessage(prompt, 'user');
          promptInput.value = '';
          promptInput.focus();

          // assistentin kupla (tyhjä aluksi)
          const assistantDiv = appendMessage('', 'assistant');

          sendBtn.disabled = true;
          modelSelect.disabled = true;
          statusLine.textContent = 'Thinking...';

          // tähän kerätään koko assistentin teksti, jota lopuksi renderöidään Markdownina
          let assistantText = '';

          try {{
            const formData = new FormData();
            formData.append('model', model);
            formData.append('prompt', prompt);

            const response = await fetch('/chat_stream', {{
              method: 'POST',
              body: formData
            }});

            if (!response.ok) {{
              throw new Error('HTTP ' + response.status);
            }}

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let done = false;

            while (!done) {{
              const result = await reader.read();
              done = result.done;
              if (result.value) {{
                const chunk = decoder.decode(result.value, {{ stream: true }});
                assistantText += chunk;

                // Tarkista oliko käyttäjä alareunassa ENNEN päivitystä
                const isAtBottom =
                  chatContainer.scrollHeight - chatContainer.scrollTop - chatContainer.clientHeight < 20;

                // Päivitä kuplan raakateksti streamin aikana
                assistantDiv.textContent = assistantText;

                // Scrollaa alas vain jos käyttäjä oli jo alhaalla
                if (isAtBottom) {{
                  chatContainer.scrollTop = chatContainer.scrollHeight;
                }}
              }}
            }}

            // Streami valmis → renderöidään markdown + kaavat
            if (window.marked) {{
              const html = window.marked.parse(assistantText);
              assistantDiv.innerHTML = html;
            }} else {{
              assistantDiv.textContent = assistantText;
            }}

            if (window.MathJax && window.MathJax.typesetPromise) {{
              try {{
                await MathJax.typesetPromise([assistantDiv]);
              }} catch (e) {{
                console.warn('MathJax error', e);
              }}
            }}

            statusLine.textContent = '';
          }} catch (err) {{
            assistantDiv.textContent += "\\n[Virhe: " + err + "]";
            statusLine.textContent = 'Virhe pyynnössä.';
          }} finally {{
            sendBtn.disabled = false;
            modelSelect.disabled = false;
          }}
        }}

        promptInput.addEventListener('keydown', (e) => {{
          if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {{
            e.preventDefault();
            sendMessage();
          }}
        }});

       
      </script>
    </body>
    </html>
    """
    return HTMLResponse(html)


@app.post("/power", response_class=HTMLResponse)
def power(action: str = Form(...)):
    # Ohjaus tapahtuu /power_json:in kautta, mutta pidetään tämäkin.
    res = power_json(action)
    msg = res.get("message", "")
    return f"<html><body><p>{msg}</p><p><a href='/legacy'>Takaisin</a></p></body></html>"

@app.post("/power_json")
def power_json(action: str = Form(...)):
    """
    Proxmox VM -ohjaus + huoltotila.
    action:
      - llm_on / llm_shutdown / llm_stop
      - win_on / win_shutdown / win_stop
      - maintenance_toggle (tai maintenance_on/off)
    """
    action = (action or "").strip()

    # Huoltotila
    if action == "maintenance_toggle":
        new_val = toggle_maintenance_mode()
        try:
            llm_vm = get_vm_status(settings.LLM_VM_ID)
        except Exception as e:
            llm_vm = f"ERROR: {e}"
        try:
            win_vm = get_vm_status(settings.WINDOWS_VM_ID)
        except Exception as e:
            win_vm = f"ERROR: {e}"
        return {
            "ok": True,
            "message": f"Huoltotila {'ON' if new_val else 'OFF'}",
            "llm_vm": llm_vm,
            "windows_vm": win_vm,
            "maintenance_mode": new_val,
        }

    if action in ("maintenance_on", "maintenance_off"):
        set_maintenance_mode(action == "maintenance_on")

    maintenance = get_maintenance_mode()

    # Helper to return statuses
    def _status_payload(ok: bool, message: str):
        try:
            llm_vm = get_vm_status(settings.LLM_VM_ID)
        except Exception as e:
            llm_vm = f"ERROR: {e}"
        try:
            win_vm = get_vm_status(settings.WINDOWS_VM_ID)
        except Exception as e:
            win_vm = f"ERROR: {e}"
        return {
            "ok": ok,
            "message": message,
            "llm_vm": llm_vm,
            "windows_vm": win_vm,
            "maintenance_mode": maintenance,
        }

    # LLM VM commands
    if action == "llm_on":
        # käynnistä + odota että Ollama vastaa
        ok = ensure_llm_running()
        if ok:
            return _status_payload(True, "LLM käynnistetty (Ollama vastaa).")
        return _status_payload(False, "LLM:n käynnistys epäonnistui (katso lokit).")

    if action in ("llm_shutdown", "llm_stop"):
        # estä shutdown jos selvästi kuormaa (paitsi huoltotilassa)
        if not maintenance and action == "llm_shutdown" and is_llm_server_busy():
            return _status_payload(
                False,
                "LLM näyttää olevan käytössä (CPU-kuorma korkea), shutdownia ei suoritettu."
            )
        if action == "llm_shutdown":
            ok, msg = shutdown_vm(settings.LLM_VM_ID, wait_stopped=False)
            return _status_payload(ok, f"LLM shutdown: {msg}")
        ok, msg = stop_vm(settings.LLM_VM_ID, wait_stopped=True)
        return _status_payload(ok, f"LLM force stop: {msg}")

    # Windows VM commands
    if action == "win_on":
        # exclusivity: jos LLM-VM on päällä ja enforce, estä
        if settings.ENFORCE_EXCLUSIVE_VMS:
            try:
                llm_status = get_vm_status(settings.LLM_VM_ID)
            except Exception as e:
                return _status_payload(False, f"LLM-VM statusta ei saatu: {e}")
            if llm_status == "running":
                return _status_payload(False, "LLM-VM on käynnissä. Sammuta LLM-VM ennen Windows-VM:n käynnistystä.")
        ok, msg = start_vm(settings.WINDOWS_VM_ID, wait_running=True, timeout_s=90)
        return _status_payload(ok, f"Windows start: {msg}")

    if action == "win_shutdown":
        ok, msg = shutdown_vm(settings.WINDOWS_VM_ID, wait_stopped=False)
        return _status_payload(ok, f"Windows shutdown: {msg}")

    if action == "win_stop":
        ok, msg = stop_vm(settings.WINDOWS_VM_ID, wait_stopped=True)
        return _status_payload(ok, f"Windows force stop: {msg}")

    return _status_payload(False, f"Tuntematon action: {action}")



@app.post("/chat_stream")
def chat_stream(model: str = Form(...), prompt: str = Form(...)):
    """
    Streamaa Ollaman vastauksen selaimelle token- / chunk-kerrallaan.
    Huolehtii myös siitä, että LLM-palvelin herätetään tarvittaessa.
    """
    touch_activity()

    def generate():
        # Jos LLM ei ole ylhäällä, kerro käyttäjälle ja käynnistä
        if not llm_server_up():
            yield "Herätetään LLM-palvelinta, odota hetki...\n"
            if not ensure_llm_running():
                yield "Virhe: LLM-palvelinta ei saatu käynnistettyä.\n"
                return

        url = f"http://{settings.LLM_HOST}:{settings.LLM_PORT}/api/generate"
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": True
        }
        with requests.post(url, json=payload, stream=True, timeout=1200) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                chunk = data.get("response", "")
                if chunk:
                    touch_activity()
                    yield chunk
                if data.get("done"):
                    break

    return StreamingResponse(generate(), media_type="text/plain")


# Yksinkertaiset API-pisteet VS Code / skriptejä varten

@app.post("/api/wake_llm")
def api_wake_llm():
    ok = ensure_llm_running()
    if ok:
        touch_activity()
    return {"ok": ok, "up": llm_server_up()}


@app.get("/api/gpu_telemetry")
def api_gpu_telemetry():
    return get_gpu_telemetry()


@app.get("/api/gpu_watchdog/status")
def api_gpu_watchdog_status():
    watchdog = getattr(app.state, "gpu_watchdog", None)
    if watchdog is None:
        return {"enabled": False, "mode": "disabled", "last_error": "watchdog service not initialized"}
    return watchdog.get_status()


@app.post("/api/gpu_watchdog/control")
async def api_gpu_watchdog_control(request: Request):
    watchdog = getattr(app.state, "gpu_watchdog", None)
    if watchdog is None:
        return {"ok": False, "error": "watchdog service not initialized"}

    try:
        payload = await request.json()
    except Exception:
        return {"ok": False, "error": "invalid JSON body"}

    enabled, reset_error, parse_error = parse_watchdog_control_payload(payload)
    if parse_error:
        return {"ok": False, "error": parse_error}

    if enabled is not None:
        watchdog.set_enabled(enabled)
    if reset_error:
        watchdog.reset_error()
    return {"ok": True, "status": watchdog.get_status()}


@app.post("/api/ilo_fan/set_min")
async def api_ilo_fan_set_min(request: Request):
    try:
        body = await request.json()
    except Exception:
        return {
            "ok": False,
            "xx": None,
            "command": "",
            "error": "invalid JSON body",
            "timestamp": None,
        }

    xx = body.get("xx")
    return set_ilo_fan_min(xx)


@app.get("/api/ilo_fan/status")
def api_ilo_fan_status():
    last = get_last_fan_command_result()
    if last is None:
        return {"ok": False, "message": "no iLO fan command has been executed in this process"}
    return last


@app.get("/api/status")
def api_status():
    """
    Status UI:lle.
    - llm_up: vastaako Ollama
    - llm_vm/windows_vm: Proxmox VM status
    - maintenance_mode: huoltotila
    - system_health/cpu_temp: iLO/IPMI (jos konffattu)
    """
    up = llm_server_up()

    try:
        llm_vm = get_vm_status(settings.LLM_VM_ID)
    except Exception as e:
        llm_vm = f"ERROR: {e}"
    try:
        win_vm = get_vm_status(settings.WINDOWS_VM_ID)
    except Exception as e:
        win_vm = f"ERROR: {e}"

    maintenance = get_maintenance_mode()
    health, cpu_temp = get_lo100_health_and_temp()
    comfy_last = get_comfyui_last_activity()
    comfy_err = get_comfyui_last_error()
    watchdog = getattr(app.state, "gpu_watchdog", None)
    watchdog_status = watchdog.get_status() if watchdog is not None else None

    return {
        "llm_up": up,
        "llm_vm": llm_vm,
        "windows_vm": win_vm,
        "maintenance_mode": maintenance,
        "system_health": health,
        "cpu_temp": cpu_temp,
        "comfyui_up": comfyui_up(),
        "comfyui_last_activity": comfy_last.isoformat(),
        "comfyui_last_error": comfy_err,
        "gpu_watchdog_enabled": watchdog_status.get("enabled") if watchdog_status else False,
        "gpu_watchdog_mode": watchdog_status.get("mode") if watchdog_status else "disabled",
        "gpu_watchdog_last_error": watchdog_status.get("last_error") if watchdog_status else "watchdog service not initialized",
    }

@app.post("/api/comfyui_wake")
async def api_comfyui_wake():
    ok = await ensure_comfyui_ready()
    return {"ok": ok, "up": comfyui_up(), "error": get_comfyui_last_error()}


@app.get("/api/logs")
def api_logs(lines: int = 200):
    """
    Return tail of log file for UI.
    """
    lines = max(10, min(2000, int(lines)))
    log_path = settings.LOG_FILE
    try:
        with open(log_path, "r", encoding="utf-8") as handle:
            content = handle.read().splitlines()
        tail = content[-lines:]
        return {"ok": True, "lines": tail, "path": log_path}
    except FileNotFoundError:
        return {"ok": False, "lines": [], "path": log_path, "error": "log file not found"}
    except Exception as exc:
        return {"ok": False, "lines": [], "path": log_path, "error": str(exc)}

@app.get("/api/models")
def api_models():
    """
    Palauttaa mallien tilan JSON-muodossa:
    - id
    - label
    - source
    - device
    - present_now (true/false/null)
    """
    rows = get_model_table_status()
    return {"models": rows}

@app.get("/models", response_class=HTMLResponse)
def models_page():
    """
    Yksinkertainen HTML-näkymä mallifleetille.
    """
    rows = get_model_table_status()

    def status_text(present_now):
        if present_now is True:
            return "✅ Ollamassa"
        if present_now is False:
            return "❌ Ei Ollamassa"
        return "❓ Tuntematon"

    table_rows = ""
    for r in rows:
        table_rows += f"""
          <tr>
            <td>{r["id"]}</td>
            <td>{r["source"]}</td>
            <td>{r["device"]}</td>
            <td>{status_text(r["present_now"])}</td>
            <td>{r["label"]}</td>
          </tr>
        """

    html = f"""
    <html>
      <head>
        <meta charset="utf-8" />
        <title>Mallit – LLM-agent</title>
        <style>
          body {{
            font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            max-width: 900px;
            margin: 0 auto;
            padding: 1.5rem;
            background: #f4f4f5;
          }}
          h1 {{
            margin-top: 0;
          }}
          table {{
            border-collapse: collapse;
            width: 100%;
            background: #ffffff;
            border-radius: 12px;
            overflow: hidden;
            box-shadow: 0 2px 4px rgba(0,0,0,0.06);
          }}
          th, td {{
            padding: 0.5rem 0.75rem;
            border-bottom: 1px solid #e5e7eb;
            text-align: left;
            font-size: 0.9rem;
          }}
          th {{
            background: #f3f4f6;
            font-weight: 600;
          }}
          tr:last-child td {{
            border-bottom: none;
          }}
          a {{
            color: #2563eb;
            text-decoration: none;
          }}
          a:hover {{
            text-decoration: underline;
          }}
          .top-link {{
            margin-bottom: 0.75rem;
            display: inline-block;
            font-size: 0.9rem;
          }}
        </style>
      </head>
      <body>
        <a href="/" class="top-link">← Takaisin etusivulle</a>
        <h1>Mallit ja tila</h1>
        <table>
          <thead>
            <tr>
              <th>Model ID</th>
              <th>Source</th>
              <th>Device</th>
              <th>Tilanne nyt</th>
              <th>Label</th>
            </tr>
          </thead>
          <tbody>
            {table_rows}
          </tbody>
        </table>
      </body>
    </html>
    """
    return HTMLResponse(html)

# Voit halutessasi tehdä tästä async-version:
async def ensure_llm_running_and_ready(timeout: int = 180) -> bool:
    loop = asyncio.get_running_loop()
    start = loop.time()

    # 1) Käynnistä (sync-funktio ajettu threadissa)
    ok = await loop.run_in_executor(None, ensure_llm_running)
    if not ok:
        return False

    # 2) Odota että llm_server_up() on True
    while loop.time() - start < timeout:
        up = await loop.run_in_executor(None, llm_server_up)
        if up:
            touch_activity()
            return True
        await asyncio.sleep(3)

    return False

# --- OpenAI-yhteensopivat endpointit ---

@app.get("/v1/models")
async def list_models():
    """
    Palautetaan mallilista:
    - ensisijaisesti Ollamalta (cachetettu)
    - jos ei saatavilla, käytetään viimeisintä cachea tai DEFAULT_MODELS-listaa.
    """
    data = get_models_openai_format()
    return {"object": "list", "data": data}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """
    OpenAI-yhteensopiva chat completions -endpointti.
    
    Tukee:
    - system prompt -viestit
    - temperature, top_p, max_tokens -parametrit
    - tools / function_calling -kutsut
    - streaming-vastauksia
    """
    # Luetaan alkuperäinen body
    body_bytes = await request.body()
    try:
        body = json.loads(body_bytes.decode("utf-8"))
    except Exception:
        body = {}

    # Otetaan talteen alkuperäiset parametrit
    stream = bool(body.get("stream", False))
    temperature = body.get("temperature")
    top_p = body.get("top_p")
    max_tokens = body.get("max_tokens")
    tools = body.get("tools")
    tool_choice = body.get("tool_choice")
    functions = body.get("functions")
    function_call = body.get("function_call")

    # Käsitellään messages - voidaan lisätä system prompt
    messages = body.get("messages") or []
    system_prompt = body.get("system_prompt")

    logger.info(
        "chat_completions request model=%s stream=%s messages=%s max_tokens=%s",
        body.get("model"),
        stream,
        len(messages) if isinstance(messages, list) else "n/a",
        body.get("max_tokens"),
    )

    # Jos system_prompt on annettu eikä messages sisällä system-roolia,
    # lisätään se messages-listan alkuun
    if system_prompt:
        has_system = any(msg.get("role") == "system" for msg in messages)
        if not has_system:
            messages.insert(0, {"role": "system", "content": system_prompt})
    
    # Rakennetaan upstream-payload; säilytä myös tuntemattomat kentät
    upstream_payload = dict(body)
    upstream_payload["messages"] = messages
    if "system_prompt" in upstream_payload:
        del upstream_payload["system_prompt"]

    # Legacy function calling -> tools/tool_choice (OpenAI 0613 compatibility)
    used_legacy_functions = False
    if tools is None and isinstance(functions, list):
        converted_tools = []
        for f in functions:
            if isinstance(f, dict):
                converted_tools.append({"type": "function", "function": f})
        if converted_tools:
            upstream_payload["tools"] = converted_tools
            used_legacy_functions = True
        if "functions" in upstream_payload:
            del upstream_payload["functions"]

    if tool_choice is None and function_call is not None:
        used_legacy_functions = True
        if isinstance(function_call, str):
            upstream_payload["tool_choice"] = function_call
        elif isinstance(function_call, dict):
            name = function_call.get("name")
            if name:
                upstream_payload["tool_choice"] = {
                    "type": "function",
                    "function": {"name": name},
                }
        if "function_call" in upstream_payload:
            del upstream_payload["function_call"]
    
    # Lisätään valinnaiset parametrit jos ne on määritetty
    if temperature is not None:
        # Rajoitetaan temperature välille 0.0-2.0 OpenAI-standardin mukaisesti
        temperature = max(0.0, min(2.0, float(temperature)))
        upstream_payload["temperature"] = temperature
    
    if top_p is not None:
        # Rajoitetaan top_p välille 0.0-1.0
        top_p = max(0.0, min(1.0, float(top_p)))
        upstream_payload["top_p"] = top_p
    
    if max_tokens is not None:
        # max_tokens on positiivinen kokonaisluku
        max_tokens = max(1, int(max_tokens))
        upstream_payload["max_tokens"] = max_tokens
    
    # Lisätään stream-parametri jos pyydetään
    if stream:
        upstream_payload["stream"] = True

    # 1) Varmistetaan, että llm-server on hereillä
    ready = await ensure_llm_running_and_ready()
    if not ready:
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "message": "LLM server not available",
                    "type": "server_error",
                }
            },
        )

    upstream_url = f"{settings.LLM_SERVER_BASE}/v1/chat/completions"
    headers = {"Content-Type": "application/json"}
    
    # Konvertoitu payload JSON-muotoon
    upstream_body = json.dumps(upstream_payload).encode("utf-8")

    # 2) Jos pyydetään streamausta → välitetään SSE-stream läpi
    if stream:
        async def stream_from_upstream():
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(
                    "POST",
                    upstream_url,
                    content=upstream_body,
                    headers=headers,
                ) as upstream_resp:
                    async for chunk in upstream_resp.aiter_bytes():
                        # chunk sisältää valmiiksi "data: ...\n\n" -tyyppisiä rivejä
                        yield chunk

        return StreamingResponse(
            stream_from_upstream(),
            media_type="text/event-stream",
        )

    # 3) Ei streamausta → tavallinen JSON-proxy
    async with httpx.AsyncClient(timeout=None) as client:
        upstream_resp = await client.post(
            upstream_url,
            content=upstream_body,
            headers=headers,
        )

    try:
        data = upstream_resp.json()
    except Exception:
        return JSONResponse(
            status_code=502,
            content={
                "error": {
                    "message": "Invalid response from upstream",
                    "type": "bad_gateway",
                }
            },
        )

    if used_legacy_functions and isinstance(data, dict):
        try:
            for choice in data.get("choices", []) or []:
                msg = choice.get("message") or {}
                tool_calls = msg.get("tool_calls")
                if tool_calls and "function_call" not in msg:
                    first = tool_calls[0] if isinstance(tool_calls, list) else None
                    func = first.get("function") if isinstance(first, dict) else None
                    if isinstance(func, dict):
                        msg["function_call"] = {
                            "name": func.get("name"),
                            "arguments": func.get("arguments"),
                        }
        except Exception:
            pass

    return JSONResponse(status_code=upstream_resp.status_code, content=data)


@app.post("/v1/completions")
async def completions(request: Request):
    """
    OpenAI-yhteensopiva legacy completions -endpointti.
    Muuntaa promptin chat-muotoon ja välittää /v1/chat/completions:iin.
    """
    def _chat_to_completion_response(payload: dict) -> dict:
        """Map chat-completions response to legacy completions response."""
        if not isinstance(payload, dict):
            return payload

        choices_out = []
        for choice in payload.get("choices", []) or []:
            if not isinstance(choice, dict):
                continue
            msg = choice.get("message") or {}
            if not isinstance(msg, dict):
                msg = {}
            text = msg.get("content")
            if text is None:
                delta = choice.get("delta") or {}
                if isinstance(delta, dict):
                    text = delta.get("content")
            choices_out.append(
                {
                    "index": choice.get("index", 0),
                    "text": text or "",
                    "finish_reason": choice.get("finish_reason"),
                }
            )

        return {
            "id": payload.get("id"),
            "object": "text_completion",
            "created": payload.get("created"),
            "model": payload.get("model"),
            "choices": choices_out,
            "usage": payload.get("usage"),
        }

    def _chat_chunk_to_completion_chunk(payload: dict) -> dict:
        """Map a chat-completions stream chunk to legacy completion chunk."""
        if not isinstance(payload, dict):
            return payload

        choices_out = []
        for choice in payload.get("choices", []) or []:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta") or {}
            if not isinstance(delta, dict):
                delta = {}
            text = delta.get("content") or ""
            choices_out.append(
                {
                    "index": choice.get("index", 0),
                    "text": text,
                    "finish_reason": choice.get("finish_reason"),
                }
            )

        return {
            "id": payload.get("id"),
            "object": "text_completion",
            "created": payload.get("created"),
            "model": payload.get("model"),
            "choices": choices_out,
        }
    body_bytes = await request.body()
    try:
        body = json.loads(body_bytes.decode("utf-8"))
    except Exception:
        body = {}

    stream = bool(body.get("stream", False))
    temperature = body.get("temperature")
    top_p = body.get("top_p")
    max_tokens = body.get("max_tokens")

    prompt = body.get("prompt", "")
    if isinstance(prompt, list):
        prompt = "\n".join(str(p) for p in prompt)
    elif prompt is None:
        prompt = ""

    suffix = body.get("suffix")
    suffix_len = len(suffix) if isinstance(suffix, str) else "n/a"
    contains_fim = isinstance(prompt, str) and (
        "<fim_" in prompt or "<|fim_" in prompt
    )

    # Continue (and some other clients) sometimes embed FIM tokens directly in `prompt`
    # instead of using the OpenAI `suffix` field. Ollama's Qwen templates only enable
    # true FIM mode when `suffix` is present, so we translate token-embedded prompts.
    if isinstance(prompt, str) and (suffix is None or suffix == ""):
        fim_prefix_tok = "<|fim_prefix|>"
        fim_suffix_tok = "<|fim_suffix|>"
        fim_middle_tok = "<|fim_middle|>"
        if fim_prefix_tok in prompt and fim_suffix_tok in prompt and fim_middle_tok in prompt:
            try:
                # Take everything after <|fim_prefix|> as the prefix section.
                after_prefix = prompt.split(fim_prefix_tok, 1)[1]
                prefix_text, rest = after_prefix.split(fim_suffix_tok, 1)
                suffix_text, _after_middle = rest.split(fim_middle_tok, 1)

                prefix_text = prefix_text
                suffix_text = suffix_text

                # Ollama's template uses `if .Suffix` (empty string is false),
                # so ensure suffix is non-empty even at EOF.
                if suffix_text == "":
                    suffix_text = "\n"

                body["prompt"] = prefix_text
                body["suffix"] = suffix_text
                prompt = prefix_text
                suffix = suffix_text
                suffix_len = len(suffix_text)
                contains_fim = True
                logger.info(
                    "completions: translated embedded FIM tokens into {prompt,suffix} (suffix_len=%s)",
                    suffix_len,
                )
            except Exception:
                # If parsing fails, fall back to the original payload.
                pass

    logger.info(
        "completions request model=%s stream=%s prompt_len=%s suffix_len=%s max_tokens=%s fim=%s",
        body.get("model"),
        stream,
        len(prompt) if isinstance(prompt, str) else "n/a",
        suffix_len,
        max_tokens,
        contains_fim,
    )

    ready = await ensure_llm_running_and_ready()
    if not ready:
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "message": "LLM server not available",
                    "type": "server_error",
                }
            },
        )

    # Prefer proxying the true /v1/completions endpoint upstream (best for FIM/tab autocomplete).
    upstream_payload = dict(body)
    if temperature is not None:
        upstream_payload["temperature"] = max(0.0, min(2.0, float(temperature)))
    if top_p is not None:
        upstream_payload["top_p"] = max(0.0, min(1.0, float(top_p)))
    if max_tokens is not None:
        upstream_payload["max_tokens"] = max(1, int(max_tokens))
    if stream:
        upstream_payload["stream"] = True

    upstream_url = f"{settings.LLM_SERVER_BASE}/v1/completions"
    headers = {"Content-Type": "application/json"}
    upstream_body = json.dumps(upstream_payload).encode("utf-8")

    # Streaming: pass through SSE if upstream supports /v1/completions; otherwise fallback to chat proxy.
    if stream:
        async def _stream_from_chat_fallback(prompt_text: str, original_body: dict, headers_in: dict):
            # Build a chat request from the legacy prompt and map chat SSE chunks to completion SSE chunks.
            messages = [{"role": "user", "content": str(prompt_text)}]
            system_prompt = original_body.get("system_prompt")
            if system_prompt:
                messages.insert(0, {"role": "system", "content": system_prompt})

            chat_payload = dict(original_body)
            chat_payload["messages"] = messages
            chat_payload.pop("prompt", None)
            chat_payload.pop("system_prompt", None)
            chat_payload["stream"] = True

            chat_url = f"{settings.LLM_SERVER_BASE}/v1/chat/completions"
            chat_body = json.dumps(chat_payload).encode("utf-8")

            buffer = b""
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(
                    "POST",
                    chat_url,
                    content=chat_body,
                    headers=headers_in,
                ) as upstream_resp:
                    async for chunk in upstream_resp.aiter_bytes():
                        buffer += chunk
                        while b"\n" in buffer:
                            line, buffer = buffer.split(b"\n", 1)
                            line = line.strip()
                            if not line:
                                continue
                            if not line.startswith(b"data:"):
                                continue
                            data = line[5:].strip()
                            if data == b"[DONE]":
                                yield b"data: [DONE]\n\n"
                                return
                            try:
                                payload = json.loads(data.decode("utf-8"))
                            except Exception:
                                continue
                            mapped = _chat_chunk_to_completion_chunk(payload)
                            out = json.dumps(mapped).encode("utf-8")
                            yield b"data: " + out + b"\n\n"

        async def stream_from_upstream():
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(
                    "POST",
                    upstream_url,
                    content=upstream_body,
                    headers=headers,
                ) as upstream_resp:
                    if upstream_resp.status_code in (404, 405):
                        # Fallback: emulate completions via chat/completions and map stream chunks.
                        logger.info("upstream /v1/completions unsupported; falling back to chat proxy")
                        async for item in _stream_from_chat_fallback(prompt, body, headers):
                            yield item
                        return

                    async for chunk in upstream_resp.aiter_bytes():
                        yield chunk

        return StreamingResponse(stream_from_upstream(), media_type="text/event-stream")

    async with httpx.AsyncClient(timeout=None) as client:
        upstream_resp = await client.post(
            upstream_url,
            content=upstream_body,
            headers=headers,
        )

    # If upstream doesn't implement /v1/completions, fallback to chat proxy and map response format.
    if upstream_resp.status_code in (404, 405):
        logger.info("upstream /v1/completions unsupported; falling back to chat proxy")

        messages = [{"role": "user", "content": str(prompt)}]
        system_prompt = body.get("system_prompt")
        if system_prompt:
            messages.insert(0, {"role": "system", "content": system_prompt})

        chat_payload = dict(body)
        chat_payload["messages"] = messages
        chat_payload.pop("prompt", None)
        chat_payload.pop("system_prompt", None)

        chat_url = f"{settings.LLM_SERVER_BASE}/v1/chat/completions"
        chat_body = json.dumps(chat_payload).encode("utf-8")

        async with httpx.AsyncClient(timeout=None) as client:
            upstream_resp = await client.post(
                chat_url,
                content=chat_body,
                headers=headers,
            )

        try:
            data = upstream_resp.json()
        except Exception:
            return JSONResponse(
                status_code=502,
                content={
                    "error": {
                        "message": "Invalid response from upstream",
                        "type": "bad_gateway",
                    }
                },
            )

        mapped = _chat_to_completion_response(data)
        return JSONResponse(status_code=upstream_resp.status_code, content=mapped)

    try:
        data = upstream_resp.json()
    except Exception:
        return JSONResponse(
            status_code=502,
            content={
                "error": {
                    "message": "Invalid response from upstream",
                    "type": "bad_gateway",
                }
            },
        )

    # Upstream already returned a completion-shaped response; return as-is.
    return JSONResponse(status_code=upstream_resp.status_code, content=data)


@app.get("/api/embedding-models")
async def list_embedding_models():
    """
    Palauttaa lista saatavilla olevista embedding-malleista.
    Sama formaatti kuin /v1/models, mutta vain embedding-mallit.
    """
    data = get_embedding_models_openai_format()
    return {"object": "list", "data": data}


@app.post("/v1/embeddings")
async def create_embeddings(request: Request):
    """
    OpenAI-yhteensopiva embeddings-endpointti.
    
    Hyväksyy pyynnöt muodossa:
    {
      "model": "nomic-embed-text:latest",
      "input": "teksti" tai ["teksti1", "teksti2"],
      "encoding_format": "float" (oletus) tai "base64"
    }
    
    Palauttaa OpenAI-muotoon embeddings-vektorit, tuetaan batch-käsittely.
    """
    # Luetaan request-body
    body_bytes = await request.body()
    try:
        body = json.loads(body_bytes.decode("utf-8"))
    except Exception:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": "Invalid JSON in request body",
                    "type": "invalid_request_error",
                }
            },
        )
    
    # Validointi: tarvitaan model ja input
    model = body.get("model", settings.DEFAULT_EMBEDDING_MODEL).strip()
    input_data = body.get("input")
    encoding_format = body.get("encoding_format", "float").lower()
    
    if not model:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": "model field is required",
                    "type": "invalid_request_error",
                }
            },
        )
    
    if input_data is None:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": "input field is required",
                    "type": "invalid_request_error",
                }
            },
        )
    
    # Normalisoi input (voi olla string tai lista stringejä)
    if isinstance(input_data, str):
        texts = [input_data]
    elif isinstance(input_data, list):
        texts = [str(t) for t in input_data]
    else:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": "input must be a string or array of strings",
                    "type": "invalid_request_error",
                }
            },
        )
    
    # Batch size -validointi
    if len(texts) > settings.EMBEDDING_MAX_BATCH_SIZE:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": f"input array too large (max {settings.EMBEDDING_MAX_BATCH_SIZE})",
                    "type": "invalid_request_error",
                }
            },
        )
    
    # 1) Tarkista cache
    cached = get_cached_embeddings(model, texts)
    if cached is not None:
        # Cached contains the "data" array from Ollama response
        embeddings_data_array = cached
    else:
        # 2) Varmistetaan, että llm-server on hereillä
        ready = await ensure_llm_running_and_ready()
        if not ready:
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "message": "LLM server not available",
                        "type": "server_error",
                    }
                },
            )
        
        # 3) Proxaa Ollaman /v1/embeddings -endpointtiin
        upstream_url = f"{settings.LLM_SERVER_BASE}/v1/embeddings"
        headers = {"Content-Type": "application/json"}
        
        # Rakenna payload Ollaman odottamassa muodossa
        # Ollama expects "input" field (array of strings)
        upstream_payload = {
            "model": model,
            "input": texts,
        }
        
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                upstream_resp = await client.post(
                    upstream_url,
                    json=upstream_payload,
                    headers=headers,
                )
            
            if upstream_resp.status_code != 200:
                # Try to get detailed error message from Ollama
                try:
                    error_detail = upstream_resp.json()
                except Exception:
                    error_detail = upstream_resp.text
                
                return JSONResponse(
                    status_code=upstream_resp.status_code,
                    content={
                        "error": {
                            "message": f"Upstream embedding service error",
                            "type": "server_error",
                            "detail": str(error_detail),
                        }
                    },
                )
            
            data = upstream_resp.json()
            
            # Extract the data array (contains embedding objects)
            embeddings_data_array = data.get("data", [])
            
            # Cache the result
            cache_embeddings(model, texts, embeddings_data_array)
        
        except Exception as e:
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "message": f"Error calling embedding service: {str(e)}",
                        "type": "server_error",
                    }
                },
            )
    
    # 4) Return response in OpenAI format
    response_data = {
        "object": "list",
        "data": embeddings_data_array,
        "model": model,
        "usage": {
            "prompt_tokens": sum(len(t.split()) for t in texts),
            "total_tokens": sum(len(t.split()) for t in texts),
        }
    }
    
    # If base64 encoding requested, convert embeddings
    if encoding_format == "base64":
        import base64
        for item in response_data["data"]:
            if "embedding" in item and isinstance(item["embedding"], list):
                item["embedding"] = base64.b64encode(
                    json.dumps(item["embedding"]).encode("utf-8")
                ).decode("utf-8")
    
    return JSONResponse(status_code=200, content=response_data)


@app.post("/v1/images/generations")
async def images_generations(request: Request):
    """
    OpenAI-yhteensopiva image generation -endpointti (ComfyUI).
    """
    body_bytes = await request.body()
    try:
        body = json.loads(body_bytes.decode("utf-8"))
    except Exception:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": "Invalid JSON in request body",
                    "type": "invalid_request_error",
                }
            },
        )

    prompt = body.get("prompt")
    if isinstance(prompt, list):
        prompt = "\n".join(str(p) for p in prompt if p is not None)
    if not prompt:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": "prompt field is required",
                    "type": "invalid_request_error",
                }
            },
        )

    response_format = body.get("response_format", "b64_json")
    if response_format not in ("b64_json", "url"):
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": "response_format must be 'b64_json' or 'url'",
                    "type": "invalid_request_error",
                }
            },
        )

    size = body.get("size", "1024x1024")
    try:
        size_str = str(size)
        if "x" not in size_str:
            raise ValueError("size must be formatted like 1024x1024")
        width_str, height_str = size_str.lower().split("x", 1)
        if int(width_str) <= 0 or int(height_str) <= 0:
            raise ValueError("size must be positive")
    except Exception as exc:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": str(exc),
                    "type": "invalid_request_error",
                }
            },
        )
    n = int(body.get("n", 1))
    if n <= 0 or n > settings.COMFYUI_MAX_BATCH_SIZE:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": f"n must be between 1 and {settings.COMFYUI_MAX_BATCH_SIZE}",
                    "type": "invalid_request_error",
                }
            },
        )

    negative_prompt = body.get("negative_prompt", "")
    steps = max(1, int(body.get("steps", settings.COMFYUI_DEFAULT_STEPS)))
    cfg_scale = max(0.0, float(body.get("cfg_scale", settings.COMFYUI_DEFAULT_CFG_SCALE)))
    seed = int(body.get("seed", int(time.time())))
    sampler_name = body.get("sampler", settings.COMFYUI_DEFAULT_SAMPLER)
    scheduler = body.get("scheduler", settings.COMFYUI_DEFAULT_SCHEDULER)
    checkpoint_name = body.get("model") or settings.COMFYUI_DEFAULT_CHECKPOINT or None

    try:
        data = await comfyui_generate_images(
            prompt=str(prompt),
            negative_prompt=str(negative_prompt),
            size=size_str,
            batch_size=n,
            steps=steps,
            cfg_scale=cfg_scale,
            seed=seed,
            sampler_name=str(sampler_name),
            scheduler=str(scheduler),
            checkpoint_name=checkpoint_name,
            response_format=response_format,
        )
    except Exception as exc:
        return JSONResponse(
            status_code=502,
            content={
                "error": {
                    "message": f"Image generation failed: {exc}",
                    "type": "bad_gateway",
                }
            },
        )

    return JSONResponse(
        content={
            "created": int(time.time()),
            "data": data,
        }
    )


@app.post("/v1/images/edits")
async def images_edits(request: Request):
    content_type = request.headers.get("content-type", "")
    image_bytes = None
    mask_bytes = None
    image_filename = "image.png"
    mask_filename = "mask.png"

    if "application/json" in content_type:
        try:
            body = await request.json()
        except Exception:
            body = {}
        prompt = str(body.get("prompt") or "refine the image")
        model = str(body.get("model") or "")
        n = int(body.get("n", 1))
        response_format = body.get("response_format", "b64_json")
        negative_prompt = str(body.get("negative_prompt") or "")
        steps = int(body.get("steps", settings.COMFYUI_DEFAULT_STEPS))
        cfg_scale = float(body.get("cfg_scale", settings.COMFYUI_DEFAULT_CFG_SCALE))
        seed = int(body.get("seed", 0))
        sampler = str(body.get("sampler", settings.COMFYUI_DEFAULT_SAMPLER))
        scheduler = str(body.get("scheduler", settings.COMFYUI_DEFAULT_SCHEDULER))
        denoise = float(body.get("denoise", settings.COMFYUI_EDIT_DENOISE))
        image_b64 = body.get("image_b64") or body.get("image")
        mask_b64 = body.get("mask_b64") or body.get("mask")

        if not image_b64:
            return JSONResponse(
                status_code=400,
                content={
                    "error": {
                        "message": "image_b64 is required for JSON edits",
                        "type": "invalid_request_error",
                    }
                },
            )

        try:
            if isinstance(image_b64, str) and "," in image_b64:
                image_b64 = image_b64.split(",", 1)[1]
            image_bytes = base64.b64decode(image_b64)
        except Exception:
            return JSONResponse(
                status_code=400,
                content={
                    "error": {
                        "message": "Invalid image_b64",
                        "type": "invalid_request_error",
                    }
                },
            )

        if mask_b64:
            try:
                if isinstance(mask_b64, str) and "," in mask_b64:
                    mask_b64 = mask_b64.split(",", 1)[1]
                mask_bytes = base64.b64decode(mask_b64)
            except Exception:
                return JSONResponse(
                    status_code=400,
                    content={
                        "error": {
                            "message": "Invalid mask_b64",
                            "type": "invalid_request_error",
                        }
                    },
                )
    else:
        form = await request.form()
        prompt = str(form.get("prompt") or "refine the image")
        model = str(form.get("model") or "")
        n = int(form.get("n") or 1)
        response_format = str(form.get("response_format") or "b64_json")
        negative_prompt = str(form.get("negative_prompt") or "")
        steps = int(form.get("steps") or settings.COMFYUI_DEFAULT_STEPS)
        cfg_scale = float(form.get("cfg_scale") or settings.COMFYUI_DEFAULT_CFG_SCALE)
        seed = int(form.get("seed") or 0)
        sampler = str(form.get("sampler") or settings.COMFYUI_DEFAULT_SAMPLER)
        scheduler = str(form.get("scheduler") or settings.COMFYUI_DEFAULT_SCHEDULER)
        denoise = float(form.get("denoise") or settings.COMFYUI_EDIT_DENOISE)

        image_file = form.get("image")
        if image_file is None and "image_file" in form:
            image_file = form.get("image_file")
        if image_file is None and "image[]" in form:
            files = form.getlist("image[]")
            image_file = files[0] if files else None
        if image_file is not None and hasattr(image_file, "read"):
            image_bytes = await image_file.read()
            image_filename = getattr(image_file, "filename", "image.png") or "image.png"

        mask_file = form.get("mask")
        if mask_file is None and "mask_file" in form:
            mask_file = form.get("mask_file")
        if mask_file is None and "mask[]" in form:
            files = form.getlist("mask[]")
            mask_file = files[0] if files else None
        if mask_file is not None and hasattr(mask_file, "read"):
            mask_bytes = await mask_file.read()
            mask_filename = getattr(mask_file, "filename", "mask.png") or "mask.png"

    if response_format not in ("b64_json", "url"):
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": "response_format must be 'b64_json' or 'url'",
                    "type": "invalid_request_error",
                }
            },
        )

    if not image_bytes:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": "image is required",
                    "type": "invalid_request_error",
                }
            },
        )

    checkpoint_name = model or settings.COMFYUI_DEFAULT_CHECKPOINT or None

    try:
        data = await comfyui_generate_image_edits(
            prompt=prompt,
            negative_prompt=negative_prompt,
            steps=max(1, int(steps)),
            cfg_scale=max(0.0, float(cfg_scale)),
            seed=int(seed) if int(seed) != 0 else int(time.time()),
            sampler_name=sampler,
            scheduler=scheduler,
            checkpoint_name=checkpoint_name,
            response_format=response_format,
            image_bytes=image_bytes,
            image_filename=image_filename,
            denoise=max(0.05, min(0.95, float(denoise))),
            mask_bytes=mask_bytes,
            mask_filename=mask_filename if mask_bytes else None,
            n=max(1, int(n)),
        )
    except Exception as exc:
        return JSONResponse(
            status_code=502,
            content={
                "error": {
                    "message": f"Image edit failed: {exc}",
                    "type": "bad_gateway",
                }
            },
        )

    return JSONResponse(
        content={
            "created": int(time.time()),
            "data": data,
        }
    )


@app.post("/v1/images/variations")
async def images_variations(request: Request):
    content_type = request.headers.get("content-type", "")
    image_bytes = None
    image_filename = "image.png"

    if "application/json" in content_type:
        try:
            body = await request.json()
        except Exception:
            body = {}
        model = str(body.get("model") or "")
        n = int(body.get("n", 1))
        response_format = body.get("response_format", "b64_json")
        steps = int(body.get("steps", settings.COMFYUI_DEFAULT_STEPS))
        cfg_scale = float(body.get("cfg_scale", settings.COMFYUI_DEFAULT_CFG_SCALE))
        seed = int(body.get("seed", 0))
        sampler = str(body.get("sampler", settings.COMFYUI_DEFAULT_SAMPLER))
        scheduler = str(body.get("scheduler", settings.COMFYUI_DEFAULT_SCHEDULER))
        denoise = float(body.get("denoise", settings.COMFYUI_EDIT_DENOISE))
        image_b64 = body.get("image_b64") or body.get("image")

        if not image_b64:
            return JSONResponse(
                status_code=400,
                content={
                    "error": {
                        "message": "image_b64 is required for JSON variations",
                        "type": "invalid_request_error",
                    }
                },
            )

        try:
            if isinstance(image_b64, str) and "," in image_b64:
                image_b64 = image_b64.split(",", 1)[1]
            image_bytes = base64.b64decode(image_b64)
        except Exception:
            return JSONResponse(
                status_code=400,
                content={
                    "error": {
                        "message": "Invalid image_b64",
                        "type": "invalid_request_error",
                    }
                },
            )
    else:
        form = await request.form()
        model = str(form.get("model") or "")
        n = int(form.get("n") or 1)
        response_format = str(form.get("response_format") or "b64_json")
        steps = int(form.get("steps") or settings.COMFYUI_DEFAULT_STEPS)
        cfg_scale = float(form.get("cfg_scale") or settings.COMFYUI_DEFAULT_CFG_SCALE)
        seed = int(form.get("seed") or 0)
        sampler = str(form.get("sampler") or settings.COMFYUI_DEFAULT_SAMPLER)
        scheduler = str(form.get("scheduler") or settings.COMFYUI_DEFAULT_SCHEDULER)
        denoise = float(form.get("denoise") or settings.COMFYUI_EDIT_DENOISE)

        image_file = form.get("image")
        if image_file is None and "image_file" in form:
            image_file = form.get("image_file")
        if image_file is not None and hasattr(image_file, "read"):
            image_bytes = await image_file.read()
            image_filename = getattr(image_file, "filename", "image.png") or "image.png"

    if response_format not in ("b64_json", "url"):
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": "response_format must be 'b64_json' or 'url'",
                    "type": "invalid_request_error",
                }
            },
        )

    if not image_bytes:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": "image is required",
                    "type": "invalid_request_error",
                }
            },
        )

    checkpoint_name = model or settings.COMFYUI_DEFAULT_CHECKPOINT or None

    try:
        data = await comfyui_generate_image_edits(
            prompt="create a variation of the image",
            negative_prompt="",
            steps=max(1, int(steps)),
            cfg_scale=max(0.0, float(cfg_scale)),
            seed=int(seed) if int(seed) != 0 else int(time.time()),
            sampler_name=sampler,
            scheduler=scheduler,
            checkpoint_name=checkpoint_name,
            response_format=response_format,
            image_bytes=image_bytes,
            image_filename=image_filename,
            denoise=max(0.05, min(0.95, float(denoise))),
            n=max(1, int(n)),
        )
    except Exception as exc:
        return JSONResponse(
            status_code=502,
            content={
                "error": {
                    "message": f"Image variation failed: {exc}",
                    "type": "bad_gateway",
                }
            },
        )

    return JSONResponse(
        content={
            "created": int(time.time()),
            "data": data,
        }
    )


@app.get("/tools/image-edit", response_class=HTMLResponse)
def image_edit_tool():
    html = """
    <html>
    <head>
      <title>Image Edit Tool</title>
      <style>
        :root {
          --bg: #f6f1e9;
          --card: #fffaf2;
          --border: #e5dccf;
          --ink: #1a1a1a;
          --muted: #6b5e54;
          --accent: #d77a3b;
        }
        * { box-sizing: border-box; }
        body {
          margin: 0;
          font-family: "Space Grotesk", "Segoe UI", sans-serif;
          background: var(--bg);
          color: var(--ink);
        }
        .page {
          max-width: 1200px;
          margin: 0 auto;
          padding: 2rem 1.5rem 3rem;
        }
        h1 { margin: 0 0 0.5rem 0; }
        .panel {
          background: var(--card);
          border: 1px solid var(--border);
          border-radius: 16px;
          padding: 1rem 1.25rem;
          margin-bottom: 1rem;
        }
        .row {
          display: grid;
          grid-template-columns: minmax(280px, 1fr) 2fr;
          gap: 1rem;
        }
        label { font-size: 0.9rem; color: var(--muted); }
        input[type="text"], textarea, select, input[type="number"] {
          width: 100%;
          padding: 0.5rem;
          border-radius: 10px;
          border: 1px solid var(--border);
          background: #fffdf8;
          font-family: inherit;
        }
        button {
          padding: 0.5rem 1rem;
          border-radius: 10px;
          border: 1px solid #d2c4b3;
          background: var(--accent);
          color: #fff6ec;
          font-weight: 600;
          cursor: pointer;
        }
        .canvas-wrap {
          position: relative;
          display: inline-block;
          border: 1px dashed #d2c4b3;
          background: #fff;
          border-radius: 12px;
          overflow: hidden;
        }
        canvas {
          display: block;
          max-width: 100%;
          height: auto;
        }
        #mask-canvas {
          opacity: 0.35;
          cursor: crosshair;
        }
        .toolbar {
          display: flex;
          gap: 0.6rem;
          flex-wrap: wrap;
          margin: 0.6rem 0;
        }
        .muted { color: var(--muted); font-size: 0.9rem; }
        .preview img {
          max-width: 100%;
          border-radius: 12px;
          border: 1px solid var(--border);
        }
        .history {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
          gap: 0.6rem;
        }
        .history img {
          width: 100%;
          border-radius: 10px;
          border: 1px solid var(--border);
          cursor: pointer;
        }
      </style>
    </head>
    <body>
      <div class="page">
        <div class="panel">
          <h1>Image Edit Tool</h1>
          <p class="muted">Lataa kuva, maalaa maski (valkoinen = muokkaa), ja lähetä edit-pyyntö.</p>
        </div>
        <div class="row">
          <div class="panel">
            <label>Image</label>
            <input type="file" id="image-input" accept="image/*" />
            <div class="toolbar">
              <label>Brush</label>
              <input type="number" id="brush-size" value="24" min="4" max="120" />
              <button type="button" id="clear-mask">Clear mask</button>
            </div>
            <label>Prompt</label>
            <textarea id="prompt" rows="6">remove selected detail, preserve layout</textarea>
            <label>Model (checkpoint filename)</label>
            <input type="text" id="model" value="__DEFAULT_CHECKPOINT__" placeholder="sd_xl_base_1.0.safetensors" />
            <div class="muted">Tyhjä = käyttää oletusta.</div>
            <label>Response format</label>
            <select id="response-format">
              <option value="b64_json">b64_json</option>
              <option value="url">url</option>
            </select>
            <label>
              <input type="checkbox" id="invert-mask" />
              Invert mask (toggle if edits happen outside selection)
            </label>
            <label>n</label>
            <input type="number" id="n" value="1" min="1" max="4" />
            <label>Denoise</label>
            <input type="number" id="denoise" value="0.7" min="0.05" max="0.95" step="0.05" />
            <label>Steps</label>
            <input type="number" id="steps" value="35" min="10" max="60" />
            <label>CFG scale</label>
            <input type="number" id="cfg-scale" value="6" min="1" max="12" step="0.5" />
            <label>Sampler</label>
            <select id="sampler">
              <option value="dpmpp_2m_sde">dpmpp_2m_sde</option>
              <option value="dpmpp_2m">dpmpp_2m</option>
              <option value="euler">euler</option>
            </select>
            <label>Scheduler</label>
            <select id="scheduler">
              <option value="normal">normal</option>
              <option value="karras">karras</option>
            </select>
            <div class="toolbar">
              <button type="button" id="send-edit">Send edit</button>
            </div>
            <div class="muted" id="status"></div>
          </div>
          <div class="panel">
            <div class="canvas-wrap">
              <canvas id="image-canvas"></canvas>
              <canvas id="mask-canvas" style="position:absolute; inset:0;"></canvas>
            </div>
            <p class="muted">Maski: valkoinen = muokkaa, musta = säilytä.</p>
            <div class="preview" id="preview"></div>
            <div class="panel" style="margin-top:1rem;">
              <div class="label">History</div>
              <div class="history" id="history"></div>
              <p class="muted">Klikkaa kuvaa käyttääksesi sitä uudeksi inputiksi.</p>
            </div>
          </div>
        </div>
      </div>
      <script>
        const imageInput = document.getElementById('image-input');
        const imageCanvas = document.getElementById('image-canvas');
        const maskCanvas = document.getElementById('mask-canvas');
        const brushSizeInput = document.getElementById('brush-size');
        const clearMaskBtn = document.getElementById('clear-mask');
        const sendBtn = document.getElementById('send-edit');
        const statusEl = document.getElementById('status');
        const previewEl = document.getElementById('preview');
        const historyEl = document.getElementById('history');
        const history = [];
        const historyLimit = 5;
        let currentImageDataUrl = null;

        const imgCtx = imageCanvas.getContext('2d');
        const maskCtx = maskCanvas.getContext('2d');
        let drawing = false;

        function setStatus(text) {
          statusEl.textContent = text || '';
        }

        function resizeCanvases(width, height) {
          imageCanvas.width = width;
          imageCanvas.height = height;
          maskCanvas.width = width;
          maskCanvas.height = height;
          maskCtx.fillStyle = 'black';
          maskCtx.fillRect(0, 0, width, height);
        }

        function drawImageToCanvas(file) {
          const img = new Image();
          img.onload = () => {
            resizeCanvases(img.width, img.height);
            imgCtx.clearRect(0, 0, img.width, img.height);
            imgCtx.drawImage(img, 0, 0);
          };
          img.src = URL.createObjectURL(file);
        }

        function drawImageFromDataUrl(dataUrl) {
          const img = new Image();
          img.onload = () => {
            resizeCanvases(img.width, img.height);
            imgCtx.clearRect(0, 0, img.width, img.height);
            imgCtx.drawImage(img, 0, 0);
            currentImageDataUrl = dataUrl;
          };
          img.src = dataUrl;
        }

        imageInput.addEventListener('change', (e) => {
          const file = e.target.files[0];
          if (!file) return;
          drawImageToCanvas(file);
        });

        function drawMask(e) {
          if (!drawing) return;
          const rect = maskCanvas.getBoundingClientRect();
          const scaleX = maskCanvas.width / rect.width;
          const scaleY = maskCanvas.height / rect.height;
          const x = (e.clientX - rect.left) * scaleX;
          const y = (e.clientY - rect.top) * scaleY;
          const radius = parseInt(brushSizeInput.value || '24', 10);
          maskCtx.fillStyle = 'white';
          maskCtx.beginPath();
          maskCtx.arc(x, y, radius, 0, Math.PI * 2);
          maskCtx.fill();
        }

        maskCanvas.addEventListener('mousedown', (e) => {
          drawing = true;
          drawMask(e);
        });
        maskCanvas.addEventListener('mousemove', drawMask);
        window.addEventListener('mouseup', () => { drawing = false; });

        clearMaskBtn.addEventListener('click', () => {
          maskCtx.fillStyle = 'black';
          maskCtx.fillRect(0, 0, maskCanvas.width, maskCanvas.height);
        });

        async function toBlob(canvas) {
          return new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
        }

        function buildMaskCanvas(invert) {
          if (!invert) return maskCanvas;
          const temp = document.createElement('canvas');
          temp.width = maskCanvas.width;
          temp.height = maskCanvas.height;
          const ctx = temp.getContext('2d');
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
        }

        async function toFileFromDataUrl(dataUrl, filename) {
          const res = await fetch(dataUrl);
          const blob = await res.blob();
          return new File([blob], filename, { type: blob.type || 'image/png' });
        }

        sendBtn.addEventListener('click', async () => {
          let imageFile = imageInput.files[0];
          if (currentImageDataUrl) {
            imageFile = await toFileFromDataUrl(currentImageDataUrl, 'history.png');
          }
          if (!imageFile) {
            setStatus('Please upload an image first.');
            return;
          }
          setStatus('Uploading...');
          const invert = document.getElementById('invert-mask').checked;
          const maskSource = buildMaskCanvas(invert);
          const maskBlob = await toBlob(maskSource);
          const form = new FormData();
          form.append('image[]', imageFile, imageFile.name || 'image.png');
          form.append('mask[]', maskBlob, 'mask.png');
          form.append('prompt', document.getElementById('prompt').value || '');
          form.append('model', document.getElementById('model').value || '');
          form.append('response_format', document.getElementById('response-format').value);
          form.append('n', document.getElementById('n').value || '1');
          form.append('denoise', document.getElementById('denoise').value || '0.35');
          form.append('steps', document.getElementById('steps').value || '35');
          form.append('cfg_scale', document.getElementById('cfg-scale').value || '6');
          form.append('sampler', document.getElementById('sampler').value || 'dpmpp_2m_sde');
          form.append('scheduler', document.getElementById('scheduler').value || 'normal');

          const resp = await fetch('/v1/images/edits', { method: 'POST', body: form });
          if (!resp.ok) {
            const text = await resp.text();
            setStatus('Error: ' + text);
            return;
          }
          const data = await resp.json();
          const entries = data.data || [];
          previewEl.innerHTML = '';
          const newImages = [];
          for (const entry of entries) {
            if (entry.url) {
              const img = document.createElement('img');
              img.src = entry.url;
              previewEl.appendChild(img);
              newImages.push(entry.url);
            } else if (entry.b64_json) {
              const img = document.createElement('img');
              img.src = 'data:image/png;base64,' + entry.b64_json;
              previewEl.appendChild(img);
              newImages.push(img.src);
            }
          }
          if (newImages.length > 0) {
            currentImageDataUrl = newImages[0];
            for (const src of newImages) {
              history.unshift(src);
            }
            while (history.length > historyLimit) history.pop();
            renderHistory();
          }
          setStatus('Done.');
        });

        function renderHistory() {
          if (!historyEl) return;
          historyEl.innerHTML = '';
          for (const src of history) {
            const img = document.createElement('img');
            img.src = src;
            img.addEventListener('click', () => {
              drawImageFromDataUrl(src);
              if (imageInput) imageInput.value = '';
            });
            historyEl.appendChild(img);
          }
        }
      </script>
    </body>
    </html>
    """
    return HTMLResponse(
        html.replace("__DEFAULT_CHECKPOINT__", settings.COMFYUI_DEFAULT_CHECKPOINT or "")
    )
