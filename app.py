from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
import requests
import json
import asyncio
import httpx

from config import settings
from lo100 import lo100_power_status, lo100_power, get_lo100_health_and_temp
from llm_server import (
    llm_server_up,
    is_llm_server_busy,
    ensure_llm_running,
    ensure_llm_running_and_ready,
    idle_shutdown_loop,
    cpu_activity_poller,
    touch_activity,
)

app = FastAPI()


def get_models():
    models = []
    try:
        r = requests.get(f"http://{settings.LLM_HOST}:{settings.LLM_PORT}/api/tags", timeout=2)
        r.raise_for_status()
        data = r.json()
        models = [m["name"] for m in data.get("models", [])]
    except Exception:
        # ei saada yhteyttä Ollamaan → käytä fallbackia
        pass

    if not models:
        models = [m.strip() for m in settings.DEFAULT_MODELS if m.strip()]

    return models


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
            touch_activity()
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
            "id": "qwen3-vl:8b", # Ladattu
            "object": "model",
            "created": 1730000000,
            "owned_by": "llm-server",
        },
        {
            "id": "qwen3-vl:235b-cloud",
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
        {
            "id": "deepseek-coder:6.7b",
            "object": "model",
            "created": 1730000000,
            "owned_by": "llm-server",
        },
        {
            "id": "deepseek-coder:1.3b",
            "object": "model",
            "created": 1730000000,
            "owned_by": "llm-server",
        },
        {
            "id": "llama3.2:latest",
            "object": "model",
            "created": 1730000000,
            "owned_by": "llm-server",
        },
        # lisää tänne niitä malleja, joita aiot käyttää
    ]
    return {"object": "list", "data": models}

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