# Lease + Proxy API - Complete Documentation

## Table of Contents
1. [Overview](#overview)
2. [Authentication](#authentication)
3. [Configuration](#configuration)
4. [API Reference](#api-reference)
5. [Client Library Example](#client-library-example)
6. [Lifecycle & Examples](#lifecycle--examples)
7. [Troubleshooting](#troubleshooting)
8. [Performance & Scaling](#performance--scaling)
9. [Security Considerations](#security-considerations)

## Overview

The Lease + Proxy API enables external applications to safely use the LLM server (Ollama) with automatic power management:

1. **Lease System**: Request a time-limited lease to use the LLM
2. **Auto Startup**: VM powers on automatically when needed
3. **Readiness**: System waits until LLM is fully operational
4. **Activity Tracking**: Keeps VM on while leases are active
5. **HTTP Proxy**: Forward any HTTP request to the LLM server
6. **Concurrent Requests**: Multiple clients can use the system simultaneously

## Authentication

If `LLM_AGENT_TOKEN` is set, lease and proxy endpoints require **Bearer token authentication**.
If it is empty/unset, auth is disabled for all endpoints (not recommended).

### Headers
```http
Authorization: Bearer YOUR_TOKEN_HERE
```

### Token Generation
```bash
# Generate a new token
python3 setup_lease_api.py --generate-token

# Store in environment or llm_secrets.py
export LLM_AGENT_TOKEN="your-generated-token"
```

### No Auth Example (if LLM_AGENT_TOKEN not configured)
If `LLM_AGENT_TOKEN` is empty/not set, auth is disabled. This is NOT RECOMMENDED for production.

## Configuration

### Auth Settings (recommended)
```bash
# Shared secret token for authentication
export LLM_AGENT_TOKEN="your-secret-token-here"
```

### Optional Settings (with defaults)
```bash
# Internal LLM server URL (used for proxy and readiness)
# Default: http://192.168.8.33:11434
export LLM_BASE_URL="http://192.168.8.33:11434"

# Endpoint to check LLM readiness
# For Ollama: /api/tags or /api/version
# Default: /api/tags
export LLM_READINESS_PATH="/api/tags"

# Default TTL for new leases (seconds)
# Default: 3600 (1 hour)
export LEASE_DEFAULT_TTL="3600"

# How long to wait for LLM to become ready (seconds)
# Default: 120
export LLM_READINESS_TIMEOUT="120"

# Polling interval when checking readiness (seconds)
# Default: 2.0
export LLM_READINESS_POLL_INTERVAL="2.0"

# Idle shutdown mode: "Off", "Medium" (2h), or "High" (30min)
# Default: Medium
export POWER_MODE="Medium"
```

## API Reference

### 1. Create Lease - POST /v1/lease

Each call creates a new lease. Use refresh to extend an existing lease.

**Request**:
```json
{
  "client_id": "string",      // Required
  "purpose": "string",        // Required
  "ttl_seconds": 3600         // Optional
}
```

**Response (201 Ready)**:
```json
{
  "lease_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "ready",
  "llm_base_url": "http://192.168.8.33:11434",
  "message": "LLM is ready"
}
```

**Response (202 Starting)**:
```json
{
  "lease_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "starting",
  "llm_base_url": "http://192.168.8.33:11434",
  "retry_after_ms": 5000,
  "message": "LLM is starting, please retry"
}
```

If you keep receiving 202, check `/v1/health` and the llm-agent logs.

### 2. Get Lease - GET /v1/lease/{lease_id}

**Response**:
```json
{
  "lease_id": "550e8400-e29b-41d4-a716-446655440000",
  "client_id": "my-app",
  "purpose": "chat",
  "status": "ready",
  "ttl_seconds": 3600,
  "created_at": "2025-12-21T10:30:00.000000",
  "last_seen": "2025-12-21T10:35:15.123456",
  "expires_at": "2025-12-21T11:30:00.000000"
}
```

### 3. Refresh Lease - POST /v1/lease/{lease_id}/refresh

**Request** (optional):
```json
{
  "ttl_seconds": 7200
}
```

**Response**: Updated lease (same as GET)

### 4. Release Lease - POST /v1/lease/{lease_id}/release

**Response**:
```json
{
  "success": true,
  "message": "Lease 550e8400-e29b-41d4-a716-446655440000 released"
}
```

### 5. Health Check - GET /v1/health

Auth is required only when `LLM_AGENT_TOKEN` is set.

**Response**:
```json
{
  "ok": true,
  "vm_state": "running",
  "llm_ready": true,
  "active_leases": 2,
  "message": "All systems operational"
}
```

`vm_state` is typically `running` or `stopped`, but may include an error string if the Proxmox status lookup fails.

### 6. Proxy - {GET|POST|PUT|PATCH|DELETE} /v1/proxy/{path}

Forwards requests to the LLM with optional `X-Lease-Id` header.
The proxy does not start the LLM VM; call `/v1/lease` first to warm up.
If `X-Lease-Id` is provided, it must be valid or the proxy returns 403.

## Client Library Example

Use standard HTTP clients (requests/httpx/curl). The API is intentionally simple.

## Quick Usage Examples

### Create Lease and Use Proxy
```python
import requests
import time

BASE = "http://localhost:8000"
TOKEN = "your-token"
headers = {"Authorization": f"Bearer {TOKEN}"}

# Create lease (returns 201 if ready or 202 if still warming)
response = requests.post(
    f"{BASE}/v1/lease",
    json={"client_id": "app", "purpose": "chat", "ttl_seconds": 3600},
    headers=headers,
)
lease_id = response.json()["lease_id"]

# Use proxy to list models
response = requests.get(
    f"{BASE}/v1/proxy/api/tags",
    headers={**headers, "X-Lease-Id": lease_id},
)
print(response.json())

# Release
requests.post(f"{BASE}/v1/lease/{lease_id}/release", headers=headers)
```

## Troubleshooting

### 401 Unauthorized
- Check token format: `Authorization: Bearer YOUR_TOKEN`
- Verify token matches `LLM_AGENT_TOKEN` env var
- Generate new token: `python3 setup_lease_api.py --generate-token`

### 503 LLM Not Ready
- Call `POST /v1/lease` first to warm up the VM
- Check readiness: `curl http://192.168.8.33:11434/api/tags`
- Verify VM is running: `qm status 101`
- Check network: `ping 192.168.8.33`

### Lease Expires Too Quickly
- Increase TTL: `ttl_seconds=7200`
- Refresh periodically: `POST /v1/lease/{id}/refresh`

### Proxy Request Hangs
- Check health: `curl http://localhost:8000/v1/health`
- Increase timeout in client
- Check LLM logs on VM

## Performance & Scaling

- **Memory**: ~1-5MB base, ~1KB per active lease
- **Concurrent clients**: No limit (each has independent lease)
- **Bottleneck**: LLM processing speed, not agent

**Optimization**:
1. Reuse leases (don't create/release per request)
2. Batch related requests
3. Set appropriate TTL (balance VM on-time vs shutdown)
4. Monitor via `/v1/health`

## Security

**Best Practices**:
- ✅ Store token securely (env var, secrets manager)
- ✅ Rotate tokens periodically  
- ✅ Use HTTPS in production (reverse proxy)
- ✅ Restrict IP access to llm-agent port
- ✅ Don't expose LLM server directly
- ✅ Never commit tokens to version control

**Audit Trail**:
- All operations logged (lease create/refresh/release)
- Client ID included for tracking
- Expired leases auto-cleaned up
