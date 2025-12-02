from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, StreamingResponse
import subprocess
import requests
import os
import json

app = FastAPI()

# Aseta nämä halutuksi (voit myös myöhemmin siirtää env-muuttujiin)
LO100_IP = os.getenv("LO100_IP", "192.168.8.33")
LO100_USER = os.getenv("LO100_USER", "admin")
LO100_PASS = os.getenv("LO100_PASS", "Azcxn669")

LLM_HOST = os.getenv("LLM_HOST", "192.168.8.31")  # llm-serverin IP
LLM_PORT = int(os.getenv("LLM_PORT", "11434"))


def llm_server_up() -> bool:
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
        out = subprocess.check_output(cmd, text=True, timeout=5)
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
        out = subprocess.check_output(cmd, text=True, timeout=10)
        return out.strip()
    except Exception as e:
        return f"ERROR: {e}"


def get_models():
    try:
        r = requests.get(f"http://{LLM_HOST}:{LLM_PORT}/api/tags", timeout=2)
        r.raise_for_status()
        data = r.json()
        return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []


@app.get("/", response_class=HTMLResponse)
def index():
    up = llm_server_up()
    power = lo100_power_status()
    models = get_models() if up else []
    html_models = "".join(
        f'<option value="{m}">{m}</option>' for m in models
    ) or '<option disabled>Ei malleja (palvelin ei päällä?)</option>'

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
        }}
        .msg {{
          padding: 0.5rem 0.75rem;
          border-radius: 8px;
          margin-bottom: 0.35rem;
          white-space: pre-wrap;
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
    </head>
    <body>
      <div class="card">
        <h1>LLM-palvelin</h1>
        <p>LO100 virran tila: <b>{power}</b></p>
        <p>LLM API: <span class="{ 'status-ok' if up else 'status-bad' }">{'UP' if up else 'DOWN'}</span></p>

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
          div.textContent = text;
          chatContainer.appendChild(div);
          chatContainer.scrollTop = chatContainer.scrollHeight;
          return div;
        }}

        async function sendMessage() {{
          const prompt = promptInput.value.trim();
          const model = modelSelect.value;
          if (!prompt || !modelSelect.value || modelSelect.disabled) return;

          // Näytä oma viesti
          appendMessage(prompt, 'user');
          promptInput.value = '';
          promptInput.focus();

          // Luodaan tyhjä assistentti-viesti johon streamataan teksti
          const assistantDiv = appendMessage('', 'assistant');

          sendBtn.disabled = true;
          modelSelect.disabled = true;
          statusLine.textContent = 'Thinking...';

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
                assistantDiv.textContent += chunk;
                chatContainer.scrollTop = chatContainer.scrollHeight;
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

        // Lähetä Ctrl+Enterillä
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
    msg = lo100_power(action)
    return f"<html><body><p>Komento: {action} → {msg}</p><p><a href='/'>Takaisin</a></p></body></html>"


@app.post("/chat_stream")
def chat_stream(model: str = Form(...), prompt: str = Form(...)):
    """
    Streamaa Ollaman vastauksen selaimelle token- / chunk-kerrallaan.
    """
    def generate():
        url = f"http://{LLM_HOST}:{LLM_PORT}/api/generate"
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": True
        }
        with requests.post(url, json=payload, stream=True, timeout=600) as r:
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
                    yield chunk
                if data.get("done"):
                    break

    return StreamingResponse(generate(), media_type="text/plain")
