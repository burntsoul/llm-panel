from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
import subprocess
import requests
import os
import json
import time
import datetime
import asyncio
import httpx
from fastapi.requests import Request


app = FastAPI()

# Konfiguraatio – voit muuttaa näitä .env:llä tai export-komennolla
LO100_IP = os.getenv("LO100_IP", "192.168.8.33")
LO100_USER = os.getenv("LO100_USER", "admin")
LO100_PASS = os.getenv("LO100_PASS", "Azcxn669")

LLM_HOST = os.getenv("LLM_HOST", "192.168.8.31")  # llm-serverin IP
LLM_PORT = int(os.getenv("LLM_PORT", "11434"))

GLANCES_API_BASE = f"http://{LLM_HOST}:61208/api/3"

# Kuorma, jota suurempana tulkitaan "LLM on käytössä" (idle-timer nollataan)
CPU_BUSY_THRESHOLD_FOR_IDLE = float(os.getenv("CPU_BUSY_THRESHOLD_FOR_IDLE", "20"))  # %
CPU_POLL_INTERVAL_SECONDS = float(os.getenv("CPU_POLL_INTERVAL_SECONDS", "10"))      # s


# Kuinka kauan odotetaan käynnistyvää LLM-palvelinta (sekunteina)
LLM_BOOT_TIMEOUT = int(os.getenv("LLM_BOOT_TIMEOUT", "180"))
LLM_POLL_INTERVAL = float(os.getenv("LLM_POLL_INTERVAL", "5"))

# Kuinka kauan ilman käyttöä ennen automaattista sammutusta (sekunteina)
LLM_IDLE_SECONDS = int(os.getenv("LLM_IDLE_SECONDS", "3600"))  # esim. 30 min

# Mitkä mallit näkyvissä, vaikka palvelin olisi sammuksissa
DEFAULT_MODELS = os.getenv(
    "DEFAULT_MODELS",
    "deepseek-coder:1.3b,deepseek-coder:6.7b"
).split(",")

_last_activity = datetime.datetime.utcnow()


def _touch_activity():
    """Merkitse, että LLM:ää juuri käytettiin."""
    global _last_activity
    _last_activity = datetime.datetime.utcnow()


def llm_server_up() -> bool:
    """Tarkista vastaako Ollama /api/tags:iin."""
    try:
        r = requests.get(f"http://{LLM_HOST}:{LLM_PORT}/api/tags", timeout=1.5)
        return r.ok
    except Exception:
        return False


def lo100_power_status() -> str:
    cmd = [
        "ipmitool",
        "-I", "lanplus",
        "-H", LO100_IP,
        "-U", LO100_USER,
        "-P", LO100_PASS,
        "chassis", "power", "status",
    ]
    try:
        out = subprocess.check_output(
            cmd, 
            text=True, 
            timeout=5,
            stderr=subprocess.DEVNULL
        )
        return out.strip()
    except Exception as e:
        return f"ERROR: {e}"


def lo100_power(action: str) -> str:
    cmd = [
        "ipmitool",
        "-I", "lanplus",
        "-H", LO100_IP,
        "-U", LO100_USER,
        "-P", LO100_PASS,
        "chassis", "power", action,
    ]
    try:
        out = subprocess.check_output(
            cmd, 
            text=True, 
            timeout=10,
            stderr=subprocess.DEVNULL
        )
        return out.strip()
    except Exception as e:
        return f"ERROR: {e}"

def get_lo100_health_and_temp():
    """
    Palauttaa (system_health, cpu_temp) LO100:n sensoreista.
    system_health: 'ok', 'warning', 'critical' tai 'unknown'
    cpu_temp: esim. '30.0 °C' tai None
    """
    cpu_temp = None
    worst_level = 0  # 0=unknown, 1=ok, 2=warning, 3=critical

    cmd = [
        "ipmitool",
        "-I", "lanplus",
        "-H", LO100_IP,
        "-U", LO100_USER,
        "-P", LO100_PASS,
        "sensor",
    ]

    try:
        out = subprocess.check_output(
            cmd,
            text=True,
            timeout=15,
            stderr=subprocess.DEVNULL,  # vaimennetaan ipmitoolin virheilmoitukset
        )
    except Exception:
        return "unknown", cpu_temp

    for line in out.splitlines():
        # ohita mahdolliset virherivit
        if not line.strip():
            continue
        if line.startswith("Get HPM.x Capabilities"):
            continue

        parts = [p.strip() for p in line.split("|")]
        # Nimi | Lukema | Yksikkö | Status | ...
        if len(parts) < 4:
            continue

        name = parts[0]
        reading = parts[1]
        units = parts[2]
        status = parts[3].lower()

        name_l = name.lower()
        reading_l = reading.lower()
        units_l = units.lower()

        # Poimi CPU0 Dmn0 Temp
        if "cpu0 dmn0 temp" in name_l and reading_l not in ("na", "unavailable"):
            if "degrees" in units_l and reading.replace(".", "", 1).isdigit():
                try:
                    temp_val = float(reading)
                    cpu_temp = f"{temp_val:.1f} °C"
                except ValueError:
                    cpu_temp = f"{reading} {units}"
            else:
                cpu_temp = f"{reading} {units}"

        # --- System health -luokittelu ---

        # Jos lukema/status on "na"/"unavailable" → ei vaikutusta healthiin
        if reading_l in ("na", "unavailable"):
            continue
        if status in ("na", "ns", "n/a", "unavailable"):
            continue

        # Discrete-sensorien heksakoodit (0x0180, 0x0080 jne.) → ohitetaan
        # jotta esim. Therm-Trip0 / Chassis / ACPI State ei turhaan pudota healthia.
        if status.startswith("0x"):
            continue

        level = 0

        # Pahat tilat
        if any(word in status for word in [
            "critical",
            "non-recoverable",
            "unrecoverable",
            "fail",
            "fault",
        ]):
            level = 3

        # Varoitukset
        elif any(word in status for word in [
            "warning",
            "non-critical",
        ]):
            level = 2

        # Normaali tilanne
        elif (
            status.startswith("ok")
            or "normal operating range" in status
        ):
            level = 1

        if level > worst_level:
            worst_level = level

    if worst_level == 0:
        health = "unknown"
    elif worst_level == 1:
        health = "ok"
    elif worst_level == 2:
        health = "warning"
    else:
        health = "critical"

    return health, cpu_temp

def get_llm_server_cpu_total():
    """
    Palauttaa llm-serverin kokonais-CPU-käytön prosentteina (0-100),
    tai None jos lukemaa ei saatu.
    """
    try:
        resp = requests.get(f"{GLANCES_API_BASE}/cpu", timeout=1.0)
        resp.raise_for_status()
        data = resp.json()
        total = data.get("total")
        if total is None:
            return None
        return float(total)
    except Exception:
        return None


def is_llm_server_busy(threshold: float = CPU_BUSY_THRESHOLD_FOR_IDLE) -> bool:
    """
    Palauttaa True jos llm-serverin CPU-kuorma ylittää threshold-%.
    Virhetilanteessa palauttaa True (fail safe, ei sammuteta sokkona).
    """
    total = get_llm_server_cpu_total()
    if total is None:
        return True
    return total >= threshold


def get_models():
    models = []
    try:
        r = requests.get(f"http://{LLM_HOST}:{LLM_PORT}/api/tags", timeout=2)
        r.raise_for_status()
        data = r.json()
        models = [m["name"] for m in data.get("models", [])]
    except Exception:
        # ei saada yhteyttä Ollamaan → käytä fallbackia
        pass

    if not models:
        models = [m.strip() for m in DEFAULT_MODELS if m.strip()]

    return models


def ensure_llm_running() -> bool:
    """
    Varmista, että LLM-palvelin on käynnissä.
    - Jos on jo UP, palauttaa True.
    - Muuten lähettää LO100:lle 'power on' ja odottaa, kunnes /api/tags vastaa
      tai timeout.
    """
    if llm_server_up():
        return True

    # Käynnistä palvelin LO100:n kautta
    lo100_power("on")

    deadline = time.time() + LLM_BOOT_TIMEOUT
    while time.time() < deadline:
        if llm_server_up():
            return True
        time.sleep(LLM_POLL_INTERVAL)

    # Viimeinen tarkistus
    return llm_server_up()


async def idle_shutdown_loop():
    """
    Taustasäie, joka tarkkailee LLM:n käyttöä ja sammuttaa sen kun sitä
    ei ole käytetty pitkään aikaan.
    """
    global _last_activity
    while True:
        await asyncio.sleep(60)  # tarkista minuutin välein
        if not llm_server_up():
            continue
        idle = (datetime.datetime.utcnow() - _last_activity).total_seconds()
        if idle > LLM_IDLE_SECONDS:
            # Yritä ensin soft shutdown
            lo100_power("soft")
            # Jos haluat varmistaa, että se oikeasti menee kiinni,
            # tänne voisi halutessa lisätä vielä odottelua ja tarvittaessa "off".

async def cpu_activity_poller():
    """
    Pollaa llm-serverin CPU-kuormaa säännöllisesti ja
    kutsuu _touch_activity(), jos kuorma on selvästi ei-idle.
    Näin idle_shutdown_loop ei laukea, kun LLM:ää käytetään
    suoraan esimerkiksi VS Codesta.
    """
    while True:
        try:
            # get_llm_server_cpu_total käyttää requestsia → ajetaan säikeessä
            total = await asyncio.to_thread(get_llm_server_cpu_total)
            if total is not None and total >= CPU_BUSY_THRESHOLD_FOR_IDLE:
                _touch_activity()
        except Exception:
            # ei kaadeta polleria yksittäiseen virheeseen
            pass

        await asyncio.sleep(CPU_POLL_INTERVAL_SECONDS)

@app.on_event("startup")
async def _startup():
    # Käynnistä idle-shutdown -looppi taustalle
    asyncio.create_task(idle_shutdown_loop())
    # Käynnistä CPU-aktiviteetin polleri
    asyncio.create_task(cpu_activity_poller())

@app.get("/", response_class=HTMLResponse)
def index():
    up = llm_server_up()
    power = lo100_power_status()
    models = get_models()
    html_models = "".join(
        f'<option value="{m}">{m}</option>' for m in models
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
        <p>LO100 virran tila: <b id="power-status">{power}</b></p>
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

        <form method="post" action="/power" class="power-buttons">
          <button name="action" value="on">Power ON</button>
          <button name="action" value="soft">Soft shutdown</button>
          <button name="action" value="off">Hard OFF</button>
        </form>
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

      <script>
        const chatContainer = document.getElementById('chat-container');
        const promptInput = document.getElementById('prompt');
        const sendBtn = document.getElementById('send-btn');
        const statusLine = document.getElementById('status-line');
        const modelSelect = document.getElementById('model');

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
            const powerEl = document.getElementById('power-status');
            const llmEl = document.getElementById('llm-api-status');
            const healthEl = document.getElementById('system-health');
            const cpuTempEl = document.getElementById('cpu-temp');

            if (powerEl && data.power) {{
              powerEl.textContent = data.power;
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
    # Estä soft/hard OFF jos LLM-serverillä on selvästi kuormaa
    if action in ("off", "soft"):
        if is_llm_server_busy():
            msg = "LLM-palvelin näyttää olevan käytössä (CPU-kuorma korkea), sammutusta ei suoritettu."
            return f"<html><body><p>{msg}</p><p><a href='/'>Takaisin</a></p></body></html>"

    msg = lo100_power(action)
    return f"<html><body><p>Komento: {action} → {msg}</p><p><a href='/'>Takaisin</a></p></body></html>"


@app.post("/chat_stream")
def chat_stream(model: str = Form(...), prompt: str = Form(...)):
    """
    Streamaa Ollaman vastauksen selaimelle token- / chunk-kerrallaan.
    Huolehtii myös siitä, että LLM-palvelin herätetään tarvittaessa.
    """
    _touch_activity()

    def generate():
        # Jos LLM ei ole ylhäällä, kerro käyttäjälle ja käynnistä
        if not llm_server_up():
            yield "Herätetään LLM-palvelinta, odota hetki...\n"
            if not ensure_llm_running():
                yield "Virhe: LLM-palvelinta ei saatu käynnistettyä.\n"
                return

        url = f"http://{LLM_HOST}:{LLM_PORT}/api/generate"
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
                    _touch_activity()
                    yield chunk
                if data.get("done"):
                    break

    return StreamingResponse(generate(), media_type="text/plain")


# Yksinkertaiset API-pisteet VS Code / skriptejä varten

@app.post("/api/wake_llm")
def api_wake_llm():
    ok = ensure_llm_running()
    if ok:
        _touch_activity()
    return {"ok": ok, "up": llm_server_up()}


@app.get("/api/status")
def api_status():
    """
    Yksinkertainen status-endpoint UI:lle.
    Palauttaa LO100 virran tilan, LLM API -statuksen,
    sekä system healthin ja CPU-lämpötilan.
    """
    up = llm_server_up()
    power = lo100_power_status()
    health, cpu_temp = get_lo100_health_and_temp()
    return {
        "llm_up": up,
        "power": power,
        "system_health": health,
        "cpu_temp": cpu_temp,
    }

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
            _touch_activity()
            return True
        await asyncio.sleep(3)

    return False

# --- OpenAI-yhteensopivat endpointit ---

@app.get("/v1/models")
async def list_models():
    """
    Palautetaan staattinen lista malleista.
    Näin WebUI näkee mallit vaikka llm-server olisi kiinni.
    """
    models = [
        {
            "id": "qwen3-vl:8b",
            "object": "model",
            "created": 1730000000,
            "owned_by": "llm-server",
        },
        {
            "id": "qwen3-coder:30b",
            "object": "model",
            "created": 1730000001,
            "owned_by": "llm-server",
        },
        {
            "id": "deepseek-coder:6.7b-16k:latest",
            "object": "model",
            "created": 1730000002,
            "owned_by": "llm-server",
        },        
        {
            "id": "deepseek-coder-v2:16b",
            "object": "model",
            "created": 1730000003,
            "owned_by": "llm-server",
        },        
        {
            "id": "deepseek-r1:8b",
            "object": "model",
            "created": 1730000004,
            "owned_by": "llm-server",
        },
        # lisää tänne niitä malleja, joita aiot käyttää
    ]
    return {"object": "list", "data": models}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """
    Varsinainen proxy: herättää serverin ja välittää pyynnön eteenpäin.
    """
    # Luetaan alkuperäinen body sellaisenaan
    body_bytes = await request.body()
    headers = dict(request.headers)

    # 1) Varmista että llm-server on pystyssä
    ready = await ensure_llm_running_and_ready()
    if not ready:
        return JSONResponse(
            status_code=503,
            content={"error": {"message": "LLM server not available", "type": "server_error"}},
        )

    # 2) Proxy eteenpäin llm-serverille
    upstream_url = f"{LLM_HOST}:{LLM_PORT}/v1/chat/completions"

    # Jos et käytä streamausta, tämä riittää:
    async with httpx.AsyncClient(timeout=None) as client:
        resp = await client.post(
            upstream_url,
            content=body_bytes,
            headers={"Content-Type": headers.get("content-type", "application/json")},
        )

    return JSONResponse(status_code=resp.status_code, content=resp.json())