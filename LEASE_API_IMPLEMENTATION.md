# Lease + Proxy API - Implementation Summary

## Overview

A robust lease and HTTP proxy system has been added to the llm-agent project. This allows external applications to safely use the LLM server even when it's powered off, with automatic VM wake-up, readiness checking, and idle shutdown management.

## Files Added

### Core Implementation

1. **lease.py** (280 lines)
   - `Lease` class: Represents a single lease with TTL, expiry tracking, and serialization
   - `LeaseManager` class: Manages leases with in-memory storage and persistent disk save
   - Global instance: `get_lease_manager()` for easy access

2. **auth.py** (90 lines)
   - Token-based authentication middleware
   - `verify_token()`: Validates bearer tokens
   - `@require_token` and `@require_token_async`: Decorators for endpoint protection

3. **lease_api.py** (400+ lines)
   - FastAPI router with all lease and proxy endpoints
   - Endpoints:
     - `POST /v1/lease` - Create/get lease (with concurrent warmup)
     - `GET /v1/lease/{lease_id}` - Get lease status
     - `POST /v1/lease/{lease_id}/refresh` - Extend lease TTL
     - `POST /v1/lease/{lease_id}/release` - Remove lease
     - `GET /v1/health` - System health with lease info
     - `GET|POST|etc /v1/proxy/{path}` - HTTP proxy with streaming support

### Tests

4. **test_lease.py** (300+ lines)
   - Unit tests for `Lease` class
   - Unit tests for `LeaseManager` class
   - Tests for persistence, expiry, refresh, serialization
   - Run with: `python -m pytest test_lease.py -v`

5. **test_lease_integration.py** (400+ lines)
   - Integration tests for all API endpoints
   - Tests authentication, authorization, and error handling
   - Tests proxy forwarding and lease lifecycle
   - Run with: `python -m pytest test_lease_integration.py -v`

### Configuration & Setup

6. **setup_lease_api.py** (100+ lines)
   - Helper script to generate tokens and show configuration
   - Usage:
     - `python3 setup_lease_api.py --generate-token`
     - `python3 setup_lease_api.py --show-config`
     - `python3 setup_lease_api.py --init`

### Documentation

7. **LEASE_API.md** (500+ lines)
   - Comprehensive API documentation
   - Configuration guide
   - Client library example with full source code
   - Lifecycle examples
   - Troubleshooting guide
   - Security considerations

## Files Modified

### 1. **app.py**
- Added import and registration of lease_api router
- No breaking changes to existing endpoints

### 2. **config.py**
- Added 10 new configuration parameters:
  - `LLM_AGENT_TOKEN` - Shared secret for auth
  - `LLM_BASE_URL` - Internal LLM server URL
  - `LLM_READINESS_PATH` - Endpoint to check readiness
  - `LEASE_DEFAULT_TTL` - Default lease time-to-live
  - `LLM_READINESS_TIMEOUT` - VM warmup timeout
  - `LLM_READINESS_POLL_INTERVAL` - Readiness check interval
  - `POWER_MODE` - Idle shutdown strategy (Off/Medium/High)
  - `POWER_MODE_IDLE_TIMEOUT` - Computed timeout based on mode

### 3. **llm_server.py**
- Added `is_llm_ready()` - Check if LLM is operational
- Added `wait_for_llm_ready()` - Async wait with exponential backoff
- Updated `idle_shutdown_loop()` - Now respects active leases:
  - VM stays ON if any active leases exist
  - Falls back to traditional idle timeout when no leases

## Key Features

### 1. Lease Management
- ✅ UUID-based lease IDs
- ✅ Client-specified TTL (time-to-live)
- ✅ Automatic expiry tracking
- ✅ Activity tracking (last_seen)
- ✅ Disk persistence (leases survive restarts)
- ✅ Thread-safe with RLock

### 2. VM Warmup & Readiness
- ✅ Automatic VM power-on
- ✅ Readiness check polling with exponential backoff
- ✅ Concurrent request handling (single warmup sequence at a time)
- ✅ Configurable timeouts and polling intervals
- ✅ Supports any readiness endpoint (default: Ollama's /api/tags)

### 3. HTTP Proxy
- ✅ Transparent forwarding (GET, POST, PUT, PATCH, DELETE)
- ✅ Streaming response support (Server-Sent Events, NDJSON)
- ✅ Header/cookie forwarding (except hop-by-hop)
- ✅ Error handling and timeouts
- ✅ Optional lease tracking via X-Lease-Id header

### 4. Authentication & Security
- ✅ Bearer token in Authorization header
- ✅ Configurable secret token
- ✅ Secure token generation helper
- ✅ Per-endpoint auth validation

### 5. Idle Shutdown Integration
- ✅ Respects active leases (VM stays ON)
- ✅ Falls back to power mode timers when no leases
- ✅ Maintains backward compatibility
- ✅ Respects maintenance mode

### 6. Health & Monitoring
- ✅ `/v1/health` endpoint with detailed status
- ✅ Active lease count tracking
- ✅ VM state reporting
- ✅ LLM readiness status
- ✅ Comprehensive logging

## Configuration Quick Start

### 1. Generate Token
```bash
cd llm-agent
python3 setup_lease_api.py --generate-token
```

### 2. Add to Environment
Option A - Set environment variables:
```bash
export LLM_AGENT_TOKEN="<generated-token>"
export LLM_BASE_URL="http://192.168.8.33:11434"
export POWER_MODE="Medium"
```

Option B - Update llm_secrets.py:
```python
# llm_secrets.py
LLM_AGENT_TOKEN = "<generated-token>"
```

### 3. Restart llm-agent
```bash
sudo systemctl restart llm-agent-prod.service
```

## Usage Example

```python
import requests
import time

BASE_URL = "http://llm-agent:8000"
TOKEN = "your-secret-token"

headers = {"Authorization": f"Bearer {TOKEN}"}

# 1. Create lease (VM auto-starts if needed)
response = requests.post(
    f"{BASE_URL}/v1/lease",
    json={
        "client_id": "my-app",
        "purpose": "chat",
        "ttl_seconds": 3600,
    },
    headers=headers,
)
lease_data = response.json()
lease_id = lease_data["lease_id"]

# Handle startup delay if needed
if response.status_code == 202:
    time.sleep(5)  # Wait for startup
    # Retry or check status

# 2. Use proxy to make requests
response = requests.post(
    f"{BASE_URL}/v1/proxy/v1/chat/completions",
    json={
        "model": "llama2",
        "messages": [{"role": "user", "content": "Hello!"}],
    },
    headers={**headers, "X-Lease-Id": lease_id},
)
print(response.json())

# 3. Keep lease alive periodically
requests.post(
    f"{BASE_URL}/v1/lease/{lease_id}/refresh",
    json={"ttl_seconds": 3600},
    headers=headers,
)

# 4. Release when done (optional)
requests.post(
    f"{BASE_URL}/v1/lease/{lease_id}/release",
    headers=headers,
)
```

## Testing

### Unit Tests
```bash
cd llm-agent
python -m pytest test_lease.py -v
```

Expected output: All tests pass (LeaseManager persistence, expiry, refresh)

### Integration Tests
```bash
python -m pytest test_lease_integration.py -v
```

Expected output: All endpoint tests pass (create, get, refresh, release, proxy)

## Backward Compatibility

✅ **All existing endpoints and behavior remain unchanged**
- Existing UI and OpenAI-compatible endpoints work as before
- Power management logic enhanced but not broken
- No migration needed
- New features are opt-in (via /v1 endpoints)

## Deployment Checklist

- [ ] Generate and store `LLM_AGENT_TOKEN` securely
- [ ] Set `LLM_BASE_URL` to correct internal IP/port
- [ ] Verify `LLM_READINESS_PATH` works (test with curl)
- [ ] Choose `POWER_MODE` (Medium/High for automatic idle shutdown)
- [ ] Review and update `LEASE_DEFAULT_TTL` if needed
- [ ] Run unit tests: `python -m pytest test_lease.py -v`
- [ ] Restart llm-agent service
- [ ] Test lease workflow manually
- [ ] Monitor logs for any issues: `journalctl -u llm-agent-prod -f`
- [ ] Document token and share with authorized apps

## Architecture Notes

### Concurrency Model
- **Lease operations**: Thread-safe with RLock
- **VM warmup**: Serialized with asyncio.Lock (only one warmup at a time)
- **Proxy requests**: Concurrent (each request independent)
- **Idle shutdown**: Checks active leases before shutting down

### Persistence Strategy
- Leases stored in-memory with periodic disk saves
- Disk format: JSON at `{STATE_PATH_DIRECTORY}/leases.json`
- Expired leases removed from disk on next save
- Survives app/VM restarts without instant forget

### Performance Characteristics
- Lease creation: O(1) with UUID assignment
- Lease lookup: O(1) hash table
- Active lease count: O(n) where n is number of active leases (cleaned on access)
- Proxy forwarding: Limited by httpx/network performance
- Readiness polling: Exponential backoff (0.5s → 3s max between attempts)

## Future Enhancements

Possible additions (not included in current implementation):
- Rate limiting per client_id
- Lease quota enforcement
- Prometheus metrics endpoint
- Database backend for scale
- Web UI for lease management
- Webhook notifications
- Advanced logging/audit trail

## Support & Troubleshooting

See [LEASE_API.md](LEASE_API.md) for:
- Detailed API documentation
- Complete client library example
- Configuration reference
- Troubleshooting guide
- Security best practices
- Performance tuning
