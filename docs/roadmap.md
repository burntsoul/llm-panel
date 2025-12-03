## 5. Roadmap / TODO

Here’s a suggested roadmap with both near-term and long-term ideas.

### 5.1 Short-term (practical improvements)

1. **Better error handling & logging**

   * Add structured logging (`logging` module) instead of silent `except Exception: pass`.
   * Log key events:

     * power on/off attempts
     * LLM wake failures
     * idle shutdown decisions
     * Glances / CPU polling failures.

2. **Config file support**

   * Allow config via a simple YAML file in addition to env vars.
   * Example: `config.yaml` with LO100, LLM host, thresholds, etc.

3. **Model presets in UI**

   * Group models into:

     * “Code (fast)”
     * “Code (deep)”
     * “General chat”
   * Save last used model in localStorage per browser.

4. **Light auth**

   * Simple access control (e.g. HTTP basic auth, or shared secret in header).
   * At least to protect power controls and LLM chat from random LAN users.

5. **Status page enhancements**

   * Add Glances data (RAM, load average) to `/api/status`.
   * Show CPU% and memory usage in the UI, maybe as small badges.
   * Add "service mode" toggle, which disables the shutdown timer

6. **Chat user experience improvement**
   * Longer responses (noted when reply cut after ~300 words)
    

### 5.2 Medium-term (features)

1. **Multi-user / profiles**

   * Basic user accounts:

     * Each user:

       * has their own chat history
       * preferred models
       * default system prompts.
   * Could be simple: users file + cookie + in-memory sessions at first.

2. **RAG prototype**

   * Build a separate `rag-service` that:

     * indexes PDFs from a directory (`/data/courses/...`)
     * uses an embeddings model to build a vector store.
   * Add frontend tab “Study (RAG)” that:

     * sends question → RAG endpoint → context + answer from LLM.

3. **VS Code integration helpers**

   * Provide a small CLI or endpoint for Continue:

     * e.g. `POST /api/vscode/wake_and_ping`
   * Document recommended model setup for VS Code:

     * `dev-fast`, `dev-main`.

4. **Health dashboard**

   * A dedicated JSON endpoint for metrics:

     * uptime, number of requests, last idle shutdown time, etc.
   * A “mini Grafana-like” status section in the UI.

### 5.3 Long-term (bigger ideas)

1. **Tool-aware agent (web + system tools)**

   * Extend the agent to:

     * expose tools like `search_web`, `read_file`, `write_task`.
   * Use a tool-calling LLM to:

     * decide when to search the internet
     * decide when to run system commands.

2. **Home Assistant integration**

   * Expose HA automations via a simple API.
   * LLM Agent becomes a “brain”:

     * “Lower living room temperature 1°C if electricity prices spike.”
     * “Explain today’s energy usage based on HA sensors.”

3. **Personal assistant for emails & tasks**

   * Periodic background job:

     * Reads emails via IMAP / Gmail API.
     * Asks LLM:

       * which ones are important
       * which ones need replies.
     * Writes a summary to a local dashboard or sends a daily digest.

4. **GPU optimization**

   * Once a GPU (e.g. Tesla P40) is installed:

     * Configure heavy models (16B/30B) as “deep mode”.
     * Keep CPU-bound models only for light tasks.
   * Tune Ollama `num_thread`, `num_ctx`, `keep_alive` per model.