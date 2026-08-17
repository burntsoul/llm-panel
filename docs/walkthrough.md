## 6. Walkthrough – How the System Behaves

### Scenario A: First use of the day (server is off)

1. LLM server is OFF (only LO100 is alive).
2. You open the **LLM Agent UI** in a browser.
3. Status card shows:

   * LO100 power: `off` (or similar).
   * LLM API: `DOWN`.
4. You type a prompt and click **Send**.
5. `/chat_stream`:

   * Calls `_touch_activity()`.
   * Sees `llm_server_up()` = False.
   * Yields text: “Waking LLM server, please wait…”.
   * Calls `ensure_llm_running()`:

     * Sends `ipmitool chassis power on` to LO100.
     * Polls Ollama `/api/tags` until it responds or `LLM_BOOT_TIMEOUT` expires.
   * Once Ollama responds:

     * Sends `POST /api/generate` to Ollama and starts streaming tokens back.
6. UI shows the answer incrementally, then formats it as Markdown (and LaTeX if any).

### Scenario B: Normal interactive use

* You send multiple prompts from the UI and/or VS Code.
* Each message or token chunk:

  * Updates `_last_activity`.
* `idle_shutdown_loop`:

  * Checks every minute → sees small idle time → does nothing.
* You can see live status:

  * LO100 power: `on`.
  * LLM API: `UP`.
  * Health: `ok`.
  * CPU temp: e.g. `30.0 °C`.

### Scenario C: Auto idle shutdown

1. You stop using the LLM (no UI or VS Code activity).
2. `llm_activity_poller()` checks the managed llama.cpp profile's `GET /slots`
   endpoint every `CPU_POLL_INTERVAL_SECONDS` (normally 10 seconds).
3. A slot with `is_processing: true`, a loading/profile-switch response, or CPU
   at or above `CPU_BUSY_THRESHOLD_FOR_IDLE` refreshes the activity timer. CPU is
   supplemental: low CPU never proves that llama.cpp is idle.
4. After `LLM_IDLE_SECONDS` (normally one hour) without observed activity:

   * `idle_shutdown_loop` sees that:

     * LLM is up
     * the most recent slot state is fresh and definitively `idle` or `no_server`
     * CPU is below the supplemental activity threshold
     * there is no active lease or maintenance hold
     * idle time exceeded threshold
   * It performs one final `GET /slots` probe. Shutdown proceeds only if that
     response is still definitively `idle` or `no_server`.
   * Calls the Proxmox graceful shutdown operation.
5. Eventually LLM server goes down, but LO100 remains accessible.

If the slot endpoint is malformed, forbidden, timed out, otherwise unavailable,
or its cached state becomes stale, the state is `unknown` and automatic shutdown
is inhibited indefinitely until a valid slot response returns. HTTP 503 while a
profile is loading also inhibits shutdown. This favors preserving in-flight work
over power savings.

### Scenario D: VS Code uses the LLM directly

1. VS Code (Continue) calls `http://LLM_HOST:11434` directly.
2. Ollama spins up a model and CPU climbs (e.g. > 50%).
3. `llm_activity_poller()`:

   * Checks managed llama.cpp slots when a provider profile is active.
   * Polls Glances `/cpu`.
   * Sees `total >= CPU_BUSY_THRESHOLD_FOR_IDLE`.
   * Calls `_touch_activity()`.
4. `idle_shutdown_loop`:

   * Idle time stays small → **no auto shutdown**.
5. If you, from the UI, hit “Soft shutdown”:

   * `/power` checks `is_llm_server_busy()`:

     * sees high CPU → refuses to shutdown and tells you the server looks busy.
