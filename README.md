# LLM Agent

LLM Agent is a small control plane for a homelab LLM setup. Its in-process GPU scheduler queues and switches text/embedding workloads between Ollama models and llama.cpp profiles, while Proxmox integration enforces GPU passthrough exclusivity. ComfyUI image generation remains independently managed.

All coordinated inference clients must connect to llm-agent. Direct access to Ollama or llama-server ports is unsupported because it bypasses queueing, capacity accounting, safe model switching, and cancellation.

## Quick Start

1. Install deps:
   ```bash
   pip install -r requirements.txt
   ```
2. Create `llm_secrets.py` in the repo root (you can copy `secrets.py.example`):
   ```py
   PROXMOX_TOKEN_ID = "user@pam!llm-agent"
   PROXMOX_TOKEN_SECRET = "..."
   # Optional: token for lease/proxy API auth
   # LLM_AGENT_TOKEN = "..."
   ```
3. Run the app:
   ```bash
   uvicorn app:app --host 0.0.0.0 --port 8000 --workers 1
   ```

The scheduler holds a process lock and intentionally rejects a second worker.

## Documentation

Start here: `docs/index.md`

Highlights:
- Overview + setup: `docs/index.md`
- Client endpoints, leases, scheduler, and queue controls: `docs/usage.md`
- Runtime walkthrough: `docs/walkthrough.md`
