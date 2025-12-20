from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
import requests
import json
import asyncio
import httpx

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
app = FastAPI()


@app.on_event("startup")
async def _startup():
    # Käynnistä idle-shutdown -looppi taustalle
    asyncio.create_task(idle_shutdown_loop())
    # Käynnistä CPU-aktiviteetin polleri
    asyncio.create_task(cpu_activity_poller())

@app.get("/", response_class=HTMLResponse)
def index():
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
      <style>
        body {{
          font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          max-width: 900px;
          margin: 0 auto;
          padding: 1.5rem;
          background: #f4f4f5;
        }}
        h1, h2 {{
          margin-top: 0;
        }}
        .card {{
          background: #ffffff;
          border-radius: 12px;
          padding: 1rem 1.25rem;
          margin-bottom: 1rem;
          box-shadow: 0 2px 4px rgba(0,0,0,0.06);
        }}
        .status-ok {{
          color: #16a34a;
          font-weight: 600;
        }}
        .status-bad {{
          color: #dc2626;
          font-weight: 600;
        }}
        .power-buttons button {{
          margin-right: 0.5rem;
        }}
        #chat-container {{
          max-height: 60vh;
          overflow-y: auto;
          border-radius: 8px;
          padding: 0.75rem;
          background: #f9fafb;
          border: 1px solid #e5e7eb;
          margin-bottom: 0.75rem;
          display: flex;
          flex-direction: column;
        }}
        .msg {{
          padding: 0.5rem 0.75rem;
          border-radius: 8px;
          margin-bottom: 0.35rem;
          white-space: pre-wrap;
          max-width: 80%;
        }}
        .msg-user {{
          background: #dbeafe;
          align-self: flex-end;
        }}
        .msg-assistant {{
          background: #e5e7eb;
          align-self: flex-start;
        }}
        .msg-system {{
          font-size: 0.85rem;
          color: #6b7280;
          align-self: center;
        }}
        #input-row {{
          display: flex;
          flex-direction: column;
          gap: 0.5rem;
        }}
        #prompt {{
          width: 100%;
          min-height: 80px;
          resize: vertical;
          font-family: inherit;
        }}
        #status-line {{
          font-size: 0.85rem;
          color: #6b7280;
          min-height: 1.2em;
        }}
        #model-select {{
          margin-bottom: 0.25rem;
        }}
        button {{
          padding: 0.4rem 0.8rem;
          border-radius: 6px;
          border: 1px solid #d4d4d8;
          background: #e5e7eb;
          cursor: pointer;
        }}
        button.primary {{
          background: #2563eb;
          color: white;
          border-color: #1d4ed8;
        }}
        button:disabled {{
          opacity: 0.6;
          cursor: default;
        }}
        .modal-overlay {{
          position: fixed;
          inset: 0;
          background: rgba(15,23,42,0.45);
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
          box-shadow: 0 10px 25px rgba(0,0,0,0.25);
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
          color: #2563eb;
          cursor: pointer;
          font: inherit;
        }}
        .link-button:hover {{
          text-decoration: underline;
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
      <div class="card">
        <h1>LLM-palvelin</h1>
        <p>
          <button class="link-button" type="button" onclick="openModelsModal()">
            Näytä mallilista ja tila
          </button>
        </p>
        <p>LLM-VM tila: <b id="llm-vm-status">{llm_vm}</b></p>
        <p>Windows-VM tila: <b id="win-vm-status">{win_vm}</b></p>
        <p>Huoltotila: <b id="maintenance-status">{'ON' if maintenance else 'OFF'}</b></p>
        <p>LLM API:
          <span id="llm-api-status" class="{ 'status-ok' if up else 'status-bad' }">
            {'UP' if up else 'DOWN'}
          </span>
        </p>
        <p>System health:
          <span id="system-health" class="status-bad">
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


      <div class="card">
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



        // Päivitä status heti ja sitten 10 s välein
        window.addEventListener('load', () => {{
          refreshStatus();
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
    return f"<html><body><p>{msg}</p><p><a href='/'>Takaisin</a></p></body></html>"

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

    return {
        "llm_up": up,
        "llm_vm": llm_vm,
        "windows_vm": win_vm,
        "maintenance_mode": maintenance,
        "system_health": health,
        "cpu_temp": cpu_temp,
    }

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
    # Luetaan alkuperäinen body sellaisenaan
    body_bytes = await request.body()
    try:
        body = json.loads(body_bytes.decode("utf-8"))
    except Exception:
        body = {}

    stream = bool(body.get("stream", False))

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

    # 2) Jos pyydetään streamausta → välitetään SSE-stream läpi
    if stream:
        async def stream_from_upstream():
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(
                    "POST",
                    upstream_url,
                    content=body_bytes,
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
            content=body_bytes,
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
        embeddings_data = cached
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
            
            # Ollama returns the response in correct OpenAI format already
            # Just use it directly (it should have "object", "data", "model", "usage")
            embeddings_data = data
            
            # Cache the result
            cache_embeddings(model, texts, data.get("data", []))
        
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
    
    # 4) Return response (Ollama already returns in correct format)
    # Just ensure model field is set correctly and add usage stats
    response_data = {
        "object": embeddings_data.get("object", "list"),
        "data": embeddings_data.get("data", []),
        "model": model,
        "usage": embeddings_data.get("usage", {
            "prompt_tokens": sum(len(t.split()) for t in texts),
            "total_tokens": sum(len(t.split()) for t in texts),
        })
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