# LLM Agent – Documentation

## 1. High-Level Overview

The **LLM Agent** system is a small “control plane” for your home-lab LLM server:

* A **FastAPI app (`app.py`)** running on a lightweight VM (the *agent*).
* A **bare-metal LLM server** (HP ProLiant ML110 G6) running:

  * Ubuntu Server
  * Ollama (for running local LLMs)
  * Glances (for system metrics via HTTP)
  * LO100 (iLO-style remote management over IPMI).

### Main responsibilities

* Provide a **web UI** to:

  * Chat with models via Ollama (streaming, markdown, LaTeX).
  * See **LLM server status** (power, API, health, CPU temp).
  * Power **ON / soft shutdown / hard OFF** the server via LO100.

* Implement **auto-power management**:

  * Wake the server when needed (if LLM is down).
  * Track activity (chat + CPU load).
  * Soft-shutdown the server after an idle period.

* Expose simple **API endpoints** to integrate with other tools (e.g. VS Code, scripts).

---

## 2. High-Level Architecture

### Components

1. **llm-agent VM (FastAPI + HTML UI)**

   * Runs `app.py` with Uvicorn.
   * Serves the web UI for humans.
   * Talks to:

     * LO100 (via `ipmitool` over LAN).
     * LLM server (Ollama HTTP API).
     * Glances HTTP API on the LLM server.

2. **LLM server (HP ML110 G6)**

   * Runs:

     * Ubuntu Server
     * **Ollama** (LLM inference)
     * **Glances** (`glances -w`) for metrics
     * **LO100** as out-of-band controller (IPMI).

3. **External clients**

   * Web browsers (for the agent UI).
   * VS Code (Continue) using the Ollama HTTP API directly.
   * Future: Home Assistant, other tools.

### Basic data flow (chat)

1. User opens **LLM Agent** UI in a browser (`/` on the agent VM).
2. User selects model, types a prompt, hits **Send**.
3. `app.py`:

   * Marks activity (for idle tracking).
   * Ensures LLM server is up:

     * If Ollama is not reachable → sends IPMI `power on` to LO100.
     * Polls Ollama `/api/tags` until it’s up or timeout.
   * Streams the request to Ollama `/api/generate`.
4. Ollama streams tokens back.
5. The FastAPI endpoint converts this to a streaming HTTP response.
6. Browser JS:

   * Updates the assistant bubble in real time.
   * Renders Markdown + MathJax when the response completes.

### Power management flow

* Background tasks in `app.py`:

  * **`idle_shutdown_loop`**:

    * Every 60s, checks “last activity” timestamp.
    * If LLM server is up AND idle for `LLM_IDLE_SECONDS` → sends `ipmitool chassis power soft`.
  * **`cpu_activity_poller`**:

    * Periodically calls `Glances /api/3/cpu`.
    * If CPU load over threshold → treats that as activity, resets idle timer.
    * This ensures that **VS Code or other clients using Ollama directly** still keep the server “alive”.

* Manual power actions from UI:

  * `/power` endpoint calls `ipmitool chassis power <action>`.
  * For `soft` / `off`, it first checks CPU load (via Glances);

    * If CPU is busy → refuses to shutdown and returns a message.

---

## 3. FastAPI Application (`app.py`) – High-Level

### Configuration

The app is configured via environment variables:

* `LO100_IP` – LO100 management interface IP.
* `LO100_USER` – LO100 username.
* `LO100_PASS` – LO100 password.
* `LLM_HOST` – IP/hostname of the LLM server where Ollama runs.
* `LLM_PORT` – Ollama port (default `11434`).
* `LLM_BOOT_TIMEOUT` – Maximum time (seconds) to wait for Ollama after powering on (default `180`).
* `LLM_POLL_INTERVAL` – Poll interval (seconds) while waiting for Ollama (default `5`).
* `LLM_IDLE_SECONDS` – Idle time until auto soft shutdown (default `1800` = 30 min).
* `DEFAULT_MODELS` – Fallback list of models if Ollama is down (comma-separated).
* `CPU_BUSY_THRESHOLD_FOR_IDLE` – CPU% above which the server is considered “busy” (default `20`).
* `CPU_POLL_INTERVAL_SECONDS` – How often to poll Glances CPU (default `10`).

### Core utility functions

* **Activity tracking**

  * `_last_activity` (global datetime).
  * `_touch_activity()` – marks “last activity” as now.

* **LLM health**

  * `llm_server_up()` – `GET /api/tags` on Ollama; returns `True` if OK.
  * `get_models()` – tries to fetch available models from Ollama;
    falls back to `DEFAULT_MODELS` when Ollama is down.

* **Power control (LO100 + ipmitool)**

  * `lo100_power_status()` – `ipmitool chassis power status`.
  * `lo100_power(action)` – `ipmitool chassis power <on|off|soft|cycle>`.
  * `get_lo100_health_and_temp()`:

    * Runs `ipmitool sensor`.
    * Parses output into:

      * `system_health`: `'ok' | 'warning' | 'critical' | 'unknown'`
      * `cpu_temp`: formatted CPU0 temperature string.

* **LLM autoboot**

  * `ensure_llm_running()`:

    * If `llm_server_up()` → returns `True`.
    * Otherwise:

      * Sends `lo100_power("on")`.
      * Polls Ollama `/api/tags` until timeout or success.
    * Returns `True` if Ollama becomes reachable.

* **Glances / CPU monitoring**

  * `GLANCES_API_BASE = f"http://{LLM_HOST}:61208/api/3"`
  * `get_llm_server_cpu_total()`:

    * `GET /cpu` from Glances
    * Returns `total` CPU% or `None` on error.
  * `is_llm_server_busy(threshold)`:

    * Uses `get_llm_server_cpu_total()`.
    * Returns `True` if CPU% ≥ threshold or if there was an error (fail-safe).

### Background tasks

Registered via `@app.on_event("startup")`:

* `idle_shutdown_loop()`:

  * Runs forever.
  * Every 60 seconds:

    * If LLM server is down → skip.
    * Else: compute idle seconds `now - _last_activity`.
    * If `idle > LLM_IDLE_SECONDS` → send `lo100_power("soft")`.

* `cpu_activity_poller()`:

  * Runs forever.
  * Every `CPU_POLL_INTERVAL_SECONDS`:

    * Calls `get_llm_server_cpu_total()` (offloaded to thread via `asyncio.to_thread`).
    * If CPU ≥ `CPU_BUSY_THRESHOLD_FOR_IDLE` → `_touch_activity()`.
  * This ensures that **direct Ollama usage** (e.g. from VS Code) keeps the idle timer alive.

### HTTP endpoints

#### 3.1 `GET /` – Main UI

* Renders an HTML page containing:

  * **Status card**:

    * LO100 power status
    * LLM API status (UP/DOWN)
    * System health (ok/warning/critical/unknown)
    * CPU0 temperature
  * **Power controls**:

    * `Power ON`
    * `Soft shutdown`
    * `Hard OFF`
  * **Chat panel**:

    * Model dropdown (based on `get_models()`).
    * Multiline prompt textarea.
    * Chat message history (user + assistant bubbles).
    * Streaming answer display.
    * Markdown + MathJax rendering.

* JavaScript:

  * `refreshStatus()`:

    * Calls `/api/status`.
    * Updates DOM: power, LLM status, health, CPU temp.
  * Periodically calls `refreshStatus()` every 10s.
  * `sendMessage()`:

    * Appends user message bubble.
    * Sends `model` + `prompt` via `POST /chat_stream`.
    * Reads streaming response with `ReadableStream`.
    * Builds assistant text as it arrives.
    * Renders Markdown when stream ends.
    * Auto-scrolls only if user was at the bottom before new data.

#### 3.2 `POST /power` – Manual power commands

* Receives `action` form field.
* For `action in ("off", "soft")`:

  * Calls `is_llm_server_busy()`.
  * If busy → does **not** send IPMI command; returns an HTML page explaining that the server looks busy (high CPU) and shutdown was skipped.
* Otherwise:

  * Executes `lo100_power(action)`.
  * Returns a minimal HTML confirmation.

#### 3.3 `POST /chat_stream` – Streaming chat endpoint

* Form fields: `model`, `prompt`.
* Steps:

  1. Calls `_touch_activity()`.
  2. If `llm_server_up()` is `False`:

     * Yields a short message: `"Herätetään LLM-palvelinta, odota hetki...\n"`.
     * Calls `ensure_llm_running()`.
     * If still not up → returns an error message and stops.
  3. Calls Ollama:

     * `POST /api/generate` with JSON payload:

       ```json
       {
         "model": "<model>",
         "prompt": "<prompt>",
         "stream": true
       }
       ```
     * Iterates over `r.iter_lines()` (SSE-like JSON lines).
     * For each JSON chunk:

       * Appends `data["response"]` to output.
       * Calls `_touch_activity()` again (any LLM activity resets idle timer).
  4. Returns `StreamingResponse` with `media_type="text/plain"`.

#### 3.4 `POST /api/wake_llm`

* For programmatic (VS Code / scripts) wake-up.
* Calls `ensure_llm_running()`.
* If successful → `_touch_activity()`.
* Returns JSON:

  ```json
  { "ok": true/false, "up": true/false }
  ```

#### 3.5 `GET /api/status`

* Used by the web UI (and can be used by external tools).
* Returns JSON:

  ```json
  {
    "llm_up": <bool>,
    "power": "<ipmitool chassis power status output or ERROR>",
    "system_health": "ok|warning|critical|unknown",
    "cpu_temp": "30.0 °C" or null
  }
  ```

---

## 4. OS / Infrastructure Documentation

### LLM Server (HP ML110 G6)

* **OS**: Ubuntu Server (minimal).

* **Services**:

  1. **Ollama**

     * Installed under `/usr/local/bin/ollama`.
     * Systemd service: `ollama.service`.
     * Override configured to bind on all interfaces:

       ```ini
       ExecStart=/usr/local/bin/ollama serve --host 0.0.0.0:11434
       ```
  2. **Glances (web mode)**:

     * Installed via `apt install glances`.
     * Systemd unit, e.g. `/etc/systemd/system/glances-web.service`:

       ```ini
       [Unit]
       Description=Glances in web server mode
       After=network-online.target
       Wants=network-online.target

       [Service]
       ExecStart=/usr/bin/glances -w --bind 0.0.0.0
       Restart=on-failure

       [Install]
       WantedBy=multi-user.target
       ```
     * API endpoint:

       * CPU: `http://<LLM_HOST>:61208/api/3/cpu`
  3. **LO100 / IPMI**:

     * Remote management interface configured with a static IP.
     * `ipmitool` installed on the **agent VM**, not necessarily on the server.

  4. **Docker**
     * open-webui -p3000:8080

* **Firewall**:

  * Allow from trusted LAN only:

    * `11434/tcp` (Ollama)
    * `61208/tcp` (Glances)
    * SSH, if needed.

### llm-agent VM

* **OS**: Some Linux VM (e.g. Ubuntu Server).

* **Python environment**:

  * Python 3.12+
  * Virtualenv at `~/projects/llm-panel/.venv`
  * Dependencies:

    * `fastapi`
    * `uvicorn[standard]`
    * `requests`
    * `ipmitool` (system package, not pip)

* **Service**:

  * Can be run manually:

    ```bash
    cd ~/projects/llm-panel
    source .venv/bin/activate
    uvicorn app:app --host 0.0.0.0 --port 8000
    ```
  * Or as a systemd service (recommended) with environment variables set.
    systemd service is in use
      * Restart service with command 'sudo systemctl restart llm-agent'

* **Network**:

  * Must reach:

    * LO100 IP (IPMI, usually port 623/udp or via `ipmitool lanplus`).
    * LLM server ports: `11434`, `61208`.
