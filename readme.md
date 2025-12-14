# LLM Agent – Documentation (Proxmox VM -infra)

## 1. Overview

**LLM Agent** is a small control plane for your homelab LLM setup:

- **llm-agent VM (VMID 102)**: FastAPI + HTML UI + OpenAI-compatible proxy endpoints
- **llm-server VM (VMID 101)**: Ubuntu + Ollama + Glances
- **windows11 VM (VMID 103)**: gaming/desktop VM (GPU passthrough)
- **Proxmox host (node: proxmox)**: orchestration via Proxmox REST API

The agent can:
- start/stop the LLM VM (and wait until Ollama is reachable)
- start/stop the Windows VM
- enforce exclusivity so the GPU passthrough is not used by both at the same time
- auto-shutdown LLM VM after idle (disabled in maintenance mode)
- proxy `/v1/chat/completions` to Ollama

## 2. Network / IDs

- proxmox host: `192.168.8.31` (node: `proxmox`)
- llm-agent: `192.168.8.32` (VMID 102)
- llm-server: `192.168.8.33` (VMID 101)
- windows11: `192.168.8.34` (VMID 103)

## 3. Configuration

### 3.1 Non-secrets: `config.py`

`config.py` contains defaults and allows env overrides (VMIDs, IPs, thresholds, etc.)

### 3.2 Secrets: `secrets.py`

Create/edit `secrets.py` in the project root:

```py
PROXMOX_TOKEN_ID = "user@pam!llm-agent"
PROXMOX_TOKEN_SECRET = "..."
# optional iLO/IPMI
# ILO_IP="..."
# ILO_USER="..."
# ILO_PASS="..."
```

> Do not commit secrets.py.

## 4. Proxmox permissions (token)

Create an API token in Proxmox for the user you want the agent to use.
The token must have permissions to query/start/shutdown the target VMs.

## 5. Maintenance mode

Maintenance mode is persisted in `state.json` (default path: next to config.py).
When maintenance mode is ON:
- the automatic idle shutdown does not power down the LLM VM
- manual controls still work

## 6. Run

Example:

```bash
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000
```
