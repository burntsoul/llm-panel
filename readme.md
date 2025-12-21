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
## 7. Lease + Proxy API (NEW)

A new robust lease + proxy API has been added for external applications to safely use the LLM server with automatic power management.

### 7.1 Quick Start

1. **Generate token**:
   ```bash
   python3 setup_lease_api.py --generate-token
   ```

2. **Set environment**:
   ```bash
   export LLM_AGENT_TOKEN="your-generated-token"
   ```

3. **Restart service**:
   ```bash
   sudo systemctl restart llm-agent-prod.service
   ```

4. **Use the API**:
   ```bash
   # Create lease
   TOKEN="your-token"
   curl -X POST http://localhost:8000/v1/lease \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"client_id": "my-app", "purpose": "chat", "ttl_seconds": 3600}'
   ```

### 7.2 Key Features

- ✅ **Lease Management**: Time-limited access tokens with auto-expiry
- ✅ **Auto VM Startup**: LLM VM powers on automatically when needed
- ✅ **Readiness Checking**: Waits until LLM is fully operational
- ✅ **Activity Tracking**: Keeps VM on while leases are active
- ✅ **HTTP Proxy**: Forward requests to LLM with transparent forwarding
- ✅ **Streaming Support**: Server-Sent Events and NDJSON responses
- ✅ **Token Authentication**: Bearer token security
- ✅ **Concurrent Leases**: Multiple clients supported

### 7.3 API Endpoints

All endpoints require `Authorization: Bearer token` header.

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/v1/lease` | Create lease (auto-starts VM if needed) |
| GET | `/v1/lease/{lease_id}` | Get lease status |
| POST | `/v1/lease/{lease_id}/refresh` | Extend lease TTL |
| POST | `/v1/lease/{lease_id}/release` | Remove lease |
| GET | `/v1/health` | System health + active lease count |
| * | `/v1/proxy/{path}` | Forward HTTP request to LLM |

### 7.4 Configuration

New environment variables (all optional with defaults):
```bash
LLM_AGENT_TOKEN=<token>                   # Bearer token for auth
LLM_BASE_URL=http://192.168.8.33:11434    # LLM server internal URL
LLM_READINESS_PATH=/api/tags              # Readiness check endpoint
LEASE_DEFAULT_TTL=3600                    # Default lease duration (seconds)
LLM_READINESS_TIMEOUT=120                 # VM warmup timeout (seconds)
POWER_MODE=Medium                         # Idle shutdown strategy (Off/Medium/High)
```

### 7.5 Documentation

- [LEASE_API_REFERENCE.md](LEASE_API_REFERENCE.md) - Quick reference + examples
- [LEASE_API_IMPLEMENTATION.md](LEASE_API_IMPLEMENTATION.md) - Implementation details
- [IMPLEMENTATION_SUMMARY.md](IMPLEMENTATION_SUMMARY.md) - Feature summary
- [DEPLOYMENT_CHECKLIST.md](DEPLOYMENT_CHECKLIST.md) - Deployment guide
- [CHANGES.md](CHANGES.md) - Complete file inventory

### 7.6 Python Client Example

```python
import requests

client = LLMClient("http://localhost:8000", token="your-token")

# Create lease (VM auto-starts if needed)
lease = client.create_lease("my-app", "chat", ttl_seconds=3600)

# Use proxy to make requests
response = client.proxy_post(
    "/v1/chat/completions",
    json={
        "model": "llama2",
        "messages": [{"role": "user", "content": "Hello!"}],
    },
    lease_id=lease.lease_id,
)
print(response.json())

# Release lease
client.release_lease(lease.lease_id)
```

Full client library available in LEASE_API_REFERENCE.md

### 7.7 Testing

```bash
# Run unit tests (17 tests)
python3 test_lease.py

# Run integration tests
python3 test_lease_integration.py
```

### 7.8 Backward Compatibility

✅ All existing endpoints and functionality remain unchanged.
New lease/proxy features are completely opt-in and don't affect existing operations.