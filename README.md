# LLM Agent

LLM Agent is a small control plane for a homelab LLM setup: it manages Proxmox VMs, enforces GPU passthrough exclusivity, and proxies OpenAI-compatible requests to Ollama. It can also proxy OpenAI image generation requests to ComfyUI with on-demand startup.

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
   uvicorn app:app --host 0.0.0.0 --port 8000
   ```

## Documentation

Start here: `docs/index.md`

Highlights:
- Overview + setup: `docs/index.md`
- Lease + proxy API: `docs/lease/reference.md`
- Deployment checklist: `docs/ops/deployment_checklist.md`
