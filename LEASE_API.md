# Lease + Proxy API Documentation

## Overview

The Lease + Proxy API enables external applications to safely use the LLM server even when it's powered off. The system automatically:

1. **Wakes the LLM VM** when a client requests a lease
2. **Waits for readiness** before returning the lease as "ready"
3. **Keeps the VM on** while leases are active (respects lease TTL)
4. **Proxies HTTP requests** to the LLM server
5. **Shuts down the VM** when idle and no leases are active

## Configuration

Add the following environment variables or edit `llm_secrets.py`:

```bash
# Shared secret token for lease/proxy endpoints (required)
LLM_AGENT_TOKEN="your-secret-token-here"

# LLM server base URL (default: http://192.168.8.33:11434)
LLM_BASE_URL="http://192.168.8.33:11434"

# Readiness check endpoint (default: /api/tags)
# For Ollama, /api/tags or /api/version work well
LLM_READINESS_PATH="/api/tags"

# Default lease TTL in seconds (default: 3600 / 1 hour)
LEASE_DEFAULT_TTL=3600

# Readiness check timeout in seconds (default: 120)
LLM_READINESS_TIMEOUT=120

# Readiness check polling interval in seconds (default: 2.0)
LLM_READINESS_POLL_INTERVAL=2.0

# Power mode: "Off" (immediate), "Medium" (2h), "High" (30min)
# Determines idle timeout when no leases are active
POWER_MODE=Medium
```

## API Endpoints

All endpoints require authentication via Bearer token in the `Authorization` header:

```bash
Authorization: Bearer <LLM_AGENT_TOKEN>
```

### 1. Create/Get Lease

**POST** `/v1/lease`

Create a new lease or get a status update for the LLM server.

**Request Body:**
```json
{
  "client_id": "my-app",
  "purpose": "chat inference",
  "ttl_seconds": 3600
}
```

**Response (201 Created - LLM Ready):**
```json
{
  "lease_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "ready",
  "llm_base_url": "http://192.168.8.33:11434",
  "message": "LLM is ready"
}
```

**Response (202 Accepted - LLM Starting):**
```json
{
  "lease_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "starting",
  "llm_base_url": "http://192.168.8.33:11434",
  "retry_after_ms": 5000,
  "message": "LLM is starting, please retry"
}
```

**Status Codes:**
- `201`: Lease created and LLM ready
- `202`: Lease created but LLM still starting
- `400`: Invalid request (missing fields, invalid ttl)
- `401`: Invalid/missing authentication

**Usage:**
```python
import requests
import time

BASE_URL = "http://llm-agent:8000"
TOKEN = "your-secret-token"

headers = {"Authorization": f"Bearer {TOKEN}"}

# Create a lease
response = requests.post(
    f"{BASE_URL}/v1/lease",
    json={
        "client_id": "my-app",
        "purpose": "chat session",
        "ttl_seconds": 3600,
    },
    headers=headers,
)

if response.status_code == 202:
    # LLM is starting, retry after a delay
    lease_data = response.json()
    retry_ms = lease_data.get("retry_after_ms", 5000)
    time.sleep(retry_ms / 1000.0)
    # Retry the request or continue polling

else:
    # LLM is ready
    lease_data = response.json()
    lease_id = lease_data["lease_id"]
    llm_url = lease_data["llm_base_url"]
```

### 2. Get Lease Status

**GET** `/v1/lease/{lease_id}`

Get the current status of a lease.

**Response:**
```json
{
  "lease_id": "550e8400-e29b-41d4-a716-446655440000",
  "client_id": "my-app",
  "purpose": "chat inference",
  "status": "ready",
  "ttl_seconds": 3600,
  "created_at": "2025-01-01T12:00:00",
  "last_seen": "2025-01-01T12:05:00",
  "expires_at": "2025-01-01T13:00:00"
}
```

**Status Codes:**
- `200`: Lease found and active
- `401`: Invalid/missing authentication
- `404`: Lease not found or expired

### 3. Refresh Lease

**POST** `/v1/lease/{lease_id}/refresh`

Extend a lease's TTL and update its activity timestamp.

**Request Body (optional):**
```json
{
  "ttl_seconds": 7200
}
```

If `ttl_seconds` is omitted, the lease's existing TTL is used.

**Response:** Same as Get Lease Status

**Status Codes:**
- `200`: Lease refreshed
- `401`: Invalid/missing authentication
- `404`: Lease not found or expired

**Usage:**
```python
# Keep the lease alive
response = requests.post(
    f"{BASE_URL}/v1/lease/{lease_id}/refresh",
    json={"ttl_seconds": 3600},
    headers=headers,
)
```

### 4. Release Lease (Optional)

**POST** `/v1/lease/{lease_id}/release`

Immediately remove a lease. The VM may shut down if this was the last active lease.

**Response:**
```json
{
  "success": true,
  "message": "Lease 550e8400-e29b-41d4-a716-446655440000 released"
}
```

**Status Codes:**
- `200`: Lease released
- `401`: Invalid/missing authentication
- `404`: Lease not found

### 5. Health Check

**GET** `/v1/health`

Get system health status including VM state and active leases.

**Response:**
```json
{
  "ok": true,
  "vm_state": "running",
  "llm_ready": true,
  "active_leases": 2,
  "message": "All systems operational"
}
```

**Status Codes:**
- `200`: Health check successful
- `401`: Invalid/missing authentication (if token is configured)

### 6. HTTP Proxy

**GET|POST|PUT|PATCH|DELETE** `/v1/proxy/{path}`

Forward HTTP requests to the LLM server (e.g., Ollama).

**Headers:**
- `Authorization: Bearer <token>` (required)
- `X-Lease-Id: <lease_id>` (optional, to track request to a specific lease)

**Request/Response:**
- All request headers, query parameters, and body are forwarded
- Response status, headers, and body are returned as-is
- **Streaming responses are supported** (e.g., `stream=true` in chat completions)

**Status Codes:**
- `200`: Request forwarded successfully
- `401`: Invalid/missing authentication
- `403`: Invalid/expired lease ID
- `502`: LLM server error
- `503`: LLM server not ready
- `504`: LLM server request timeout

**Usage:**
```python
# Example: Call chat/completions via proxy
response = requests.post(
    f"{BASE_URL}/v1/proxy/v1/chat/completions",
    json={
        "model": "llama2",
        "messages": [{"role": "user", "content": "Hello!"}],
        "stream": False,
    },
    headers={
        **headers,
        "X-Lease-Id": lease_id,  # Optional but recommended
    },
)

result = response.json()
print(result)
```

**Streaming Example:**
```python
# Stream responses from LLM
response = requests.post(
    f"{BASE_URL}/v1/proxy/v1/chat/completions",
    json={
        "model": "llama2",
        "messages": [{"role": "user", "content": "Tell me a story"}],
        "stream": True,
    },
    headers={
        **headers,
        "X-Lease-Id": lease_id,
    },
    stream=True,  # Enable streaming
)

for line in response.iter_lines():
    if line:
        print(line.decode())
```

## Client Library Example

```python
import requests
import time
import logging

logger = logging.getLogger(__name__)

class LLMLeaseClient:
    """
    High-level client for using the LLM with automatic lease management.
    """

    def __init__(self, base_url: str, token: str):
        self.base_url = base_url
        self.token = token
        self.lease_id = None
        self.headers = {"Authorization": f"Bearer {token}"}

    def acquire_lease(self, client_id: str, ttl_seconds: int = 3600) -> bool:
        """
        Acquire a lease, waiting for LLM to be ready.
        
        Returns:
            True if lease acquired and LLM ready, False otherwise
        """
        max_retries = 5
        retry_count = 0

        while retry_count < max_retries:
            response = requests.post(
                f"{self.base_url}/v1/lease",
                json={
                    "client_id": client_id,
                    "purpose": f"Session from {client_id}",
                    "ttl_seconds": ttl_seconds,
                },
                headers=self.headers,
            )

            if response.status_code in (201, 202):
                self.lease_id = response.json()["lease_id"]

                if response.status_code == 201:
                    logger.info(f"Lease acquired: {self.lease_id}")
                    return True
                else:
                    # Still starting, retry after suggested delay
                    retry_after_ms = response.json().get("retry_after_ms", 5000)
                    logger.info(
                        f"LLM starting, retrying in {retry_after_ms}ms"
                    )
                    time.sleep(retry_after_ms / 1000.0)
                    retry_count += 1
            else:
                logger.error(f"Failed to acquire lease: {response.text}")
                return False

        logger.error("Max retries exceeded waiting for LLM")
        return False

    def keep_alive(self, ttl_seconds: int = 3600) -> bool:
        """
        Refresh the lease to keep it alive.
        
        Returns:
            True if successful, False otherwise
        """
        if not self.lease_id:
            return False

        response = requests.post(
            f"{self.base_url}/v1/lease/{self.lease_id}/refresh",
            json={"ttl_seconds": ttl_seconds},
            headers=self.headers,
        )

        return response.status_code == 200

    def release(self) -> bool:
        """
        Release the lease (optional).
        
        Returns:
            True if successful, False otherwise
        """
        if not self.lease_id:
            return False

        response = requests.post(
            f"{self.base_url}/v1/lease/{self.lease_id}/release",
            headers=self.headers,
        )

        if response.status_code == 200:
            self.lease_id = None
            return True
        return False

    def chat(self, model: str, messages: list, stream: bool = False):
        """
        Call the LLM chat endpoint via proxy.
        
        Args:
            model: Model name (e.g., "llama2")
            messages: List of message dicts with "role" and "content"
            stream: Whether to stream the response
            
        Returns:
            Response JSON or iterator of lines if streaming
        """
        if not self.lease_id:
            raise RuntimeError("No active lease")

        response = requests.post(
            f"{self.base_url}/v1/proxy/v1/chat/completions",
            json={
                "model": model,
                "messages": messages,
                "stream": stream,
            },
            headers={
                **self.headers,
                "X-Lease-Id": self.lease_id,
            },
            stream=stream,
        )

        if response.status_code != 200:
            raise RuntimeError(f"LLM error: {response.text}")

        if stream:
            return response.iter_lines()
        else:
            return response.json()


# Usage Example
if __name__ == "__main__":
    client = LLMLeaseClient(
        base_url="http://localhost:8000",
        token="your-secret-token",
    )

    # Acquire lease
    if not client.acquire_lease("my-app"):
        print("Failed to acquire lease")
        exit(1)

    # Use the LLM
    try:
        response = client.chat(
            model="llama2",
            messages=[{"role": "user", "content": "Hello!"}],
        )
        print("LLM Response:", response)
    finally:
        # Optionally release (will auto-expire if not released)
        client.release()
```

## Lifecycle Example

```
1. Client starts a session
   └─> Create lease with TTL=3600s
       ├─ If LLM is off: VM boots + LLM starts (up to 120s)
       ├─ Status: "starting"
       └─ Client retries after 5s

2. LLM becomes ready
   └─> Lease status changes to "ready"
   └─> Client can now use /v1/proxy to make requests

3. Client periodically refreshes lease
   └─> POST /v1/lease/{lease_id}/refresh every ~30min
   └─> VM stays ON as long as lease is active

4. Client ends session
   └─> POST /v1/lease/{lease_id}/release (or lease expires)
   └─> No more active leases

5. Idle shutdown kicks in
   └─> If POWER_MODE=Medium: 2 hours of inactivity → VM shuts down
   └─> If POWER_MODE=High: 30 minutes of inactivity → VM shuts down
```

## Lease Persistence

Leases are persisted to disk (by default: `leases.json` in the state directory) so that:

- **VM restarts don't lose active leases** – clients don't need to re-acquire
- **Activity is remembered** – the idle shutdown timer respects active leases
- **Expired leases are automatically cleaned** – no manual cleanup needed

## Security Considerations

1. **Token must be strong**: Use a cryptographically random token (e.g., `openssl rand -hex 32`)
2. **Use HTTPS in production**: The bearer token should be transmitted over encrypted connections
3. **Lease IDs are UUIDs**: Difficult to guess; don't expose to untrusted clients
4. **Proxy forwards all requests**: Ensure LLM server is only accessible via the proxy on a trusted network
5. **Monitor token usage**: Log all lease/proxy requests for audit trails

## Troubleshooting

### LLM stays in "starting" status

- Check `LLM_BASE_URL` and `LLM_READINESS_PATH` are correct
- Verify VM has enough resources and network connectivity
- Increase `LLM_READINESS_TIMEOUT` if the VM is slow to boot

### Lease expires unexpectedly

- Refresh more frequently (e.g., every 30 minutes for 1-hour TTL)
- Increase `ttl_seconds` in create/refresh requests
- Check idle shutdown is respecting the lease (logs should show it)

### Proxy requests fail with 503

- LLM may not be responding to readiness check
- Verify LLM service is running: `curl <LLM_BASE_URL><LLM_READINESS_PATH>`
- Check logs for network issues

### VM never shuts down

- Verify `MAINTENANCE_DEFAULT=false` (not in maintenance mode)
- Check for active leases: `GET /v1/health` should show `active_leases: 0`
- Verify `POWER_MODE` is set correctly and understood

## Testing

```bash
# Run unit tests
python -m pytest test_lease.py -v

# Run integration tests
python -m pytest test_lease_integration.py -v

# Or run directly
python test_lease.py
python test_lease_integration.py
```

## Performance & Limits

- **Concurrent requests**: Uses asyncio locks to serialize VM power-on sequences
- **Request timeout**: 120 seconds (configurable via `LLM_READINESS_TIMEOUT`)
- **Proxy timeout**: 120 seconds per request
- **Lease storage**: In-memory + periodic disk writes (no database required)
- **Scalability**: Suitable for single-user or small team use; not designed for multi-tenant

## Roadmap

Possible future enhancements:

- [ ] Rate limiting per client_id
- [ ] Lease quota per client
- [ ] Metrics/monitoring endpoints (Prometheus)
- [ ] Database backend for large-scale deployments
- [ ] Request/response caching for common queries
- [ ] Web UI for lease management
