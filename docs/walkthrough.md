## 6. Walkthrough – How the System Behaves

### Scenario A: First use of the day (server is off)

1. LLM server is OFF (only LO100 is alive).
2. You open the **LLM Agent UI** in a browser.
3. Status card shows:

   * LO100 power: `off` (or similar).
   * LLM API: `DOWN`.
4. You type a prompt and click **Send**.
5. `/chat_stream` enqueues a target-specific request. The scheduler starts the LLM VM if needed, waits for Ollama indefinitely using finite probes, unloads conflicting targets, preloads the requested model, and then streams the response. A temporary readiness state does not return a 503.
6. UI shows the answer incrementally, then formats it as Markdown (and LaTeX if any).

### Scenario B: Normal interactive use

* You send multiple prompts from the UI and/or VS Code through llm-agent.
* Same-target work runs up to the configured capacity. Additional work waits FIFO. An earlier request for another target creates a switch barrier, so sustained arrivals cannot starve it.
* Permits remain occupied until a non-streaming response completes or a stream closes.
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
   endpoint every `CPU_POLL_INTERVAL_SECONDS` (normally 3 minutes). Each probe
   may wait up to `LLAMA_CPP_SLOT_PROBE_TIMEOUT_SECONDS` (normally 2 minutes),
   allowing a large llama.cpp prefill batch to finish before the slot response
   is treated as unavailable.
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

### Scenario D: VS Code uses the scheduled API

1. Configure VS Code/Continue with `http://LLM_AGENT:8000/v1`, never the Ollama or llama-server port.
2. A request for another model waits while the current generation drains, then the scheduler unloads the old provider/model and verifies the new target.
3. Queued, running, switching, cancelling, or draining work inhibits VM idle shutdown.
4. Operators can inspect or recover stuck work from **Settings → Runtime → Queue**.
