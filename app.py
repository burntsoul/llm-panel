from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse
import subprocess
import requests
import os

app = FastAPI()

LO100_IP = os.getenv("LO100_IP", "192.168.8.33")
LO100_USER = os.getenv("LO100_USER", "admin")
LO100_PASS = os.getenv("LO100_PASS", "Azcxn669")
LLM_HOST = os.getenv("LLM_HOST", "192.168.8.31")
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

    return f"""
    <html>
    <head><title>LLM-ohjauspaneeli</title></head>
    <body>
      <h1>LLM-palvelin</h1>
      <p>LO100 virran tila: <b>{power}</b></p>
      <p>LLM API: <b>{"UP" if up else "DOWN"}</b></p>

      <form method="post" action="/power">
        <button name="action" value="on">Power ON</button>
        <button name="action" value="soft">Soft shutdown</button>
        <button name="action" value="off">Hard OFF</button>
      </form>

      <hr/>

      <h2>LLM-kysely</h2>
      <form method="post" action="/ask">
        <label>Malli:</label>
        <select name="model">
          {html_models}
        </select><br/><br/>
        <textarea name="prompt" rows="8" cols="80" placeholder="Kirjoita kysymys..."></textarea><br/><br/>
        <button type="submit">Lähetä</button>
      </form>
    </body>
    </html>
    """


@app.post("/power", response_class=HTMLResponse)
def power(action: str = Form(...)):
    msg = lo100_power(action)
    return f"<html><body><p>Komento: {action} → {msg}</p><p><a href='/'>Takaisin</a></p></body></html>"


@app.post("/ask", response_class=HTMLResponse)
def ask(model: str = Form(...), prompt: str = Form(...)):
    try:
        r = requests.post(
            f"http://{LLM_HOST}:{LLM_PORT}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=600,
        )
        r.raise_for_status()
        data = r.json()
        answer = data.get("response", "")
    except Exception as e:
        answer = f"Virhe: {e}"

    return f"""
    <html><body>
      <h2>Vastaus mallilta {model}</h2>
      <pre>{answer}</pre>
      <p><a href='/'>Takaisin</a></p>
    </body></html>
    """
