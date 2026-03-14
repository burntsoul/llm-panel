# LLM Agent Client Usage Guide

This document is for external repositories/projects that use the `llm-agent` service.

## 1. Service Endpoint

- Base URL: `http://192.168.8.36:8000`

Use this base URL in all examples below.

## 2. Authentication Model

- `POST /v1/lease`, `GET /v1/lease/{id}`, `POST /v1/lease/{id}/refresh`, `POST /v1/lease/{id}/release`, and `* /v1/proxy/{path}` use Bearer auth when `LLM_AGENT_TOKEN` is configured.
- `GET /v1/health` also requires Bearer auth when `LLM_AGENT_TOKEN` is configured.
- If `LLM_AGENT_TOKEN` is empty/unset, auth is disabled for those endpoints.
- Direct OpenAI-style endpoints (`/v1/chat/completions`, `/v1/completions`, `/v1/embeddings`, `/v1/images/*`, `/v1/models`) are currently not token-gated in `app.py`.

Bearer header format:

```http
Authorization: Bearer <YOUR_TOKEN>
```

## 3. Feature Overview

### Lease + Proxy (recommended for robust client integrations)

- Lease lifecycle with TTL:
  - `POST /v1/lease`
  - `GET /v1/lease/{lease_id}`
  - `POST /v1/lease/{lease_id}/refresh`
  - `POST /v1/lease/{lease_id}/release`
- Health endpoint with VM and readiness status:
  - `GET /v1/health`
- Generic HTTP proxy to upstream LLM (Ollama):
  - `GET|POST|PUT|PATCH|DELETE /v1/proxy/{path}`
- Streaming passthrough supported (SSE / NDJSON).

### OpenAI-compatible direct endpoints

- Models:
  - `GET /v1/models`
- Text generation:
  - `POST /v1/chat/completions`
  - `POST /v1/completions`
- Embeddings:
  - `POST /v1/embeddings`
- Images (ComfyUI-backed):
  - `POST /v1/images/generations`
  - `POST /v1/images/edits`
  - `POST /v1/images/variations`

## 4. Recommended Client Flow (Lease + Proxy)

1. Create a lease.
2. If status is `starting`, wait and retry the target call.
3. Send LLM requests through `/v1/proxy/...` with `X-Lease-Id`.
4. Refresh lease for long sessions.
5. Release lease when done.

### 4.1 Create Lease

```bash
TOKEN="replace-with-token"

curl -sS -X POST http://192.168.8.36:8000/v1/lease \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "client_id": "my-service",
    "purpose": "chat",
    "ttl_seconds": 3600
  }'
```

Response:
- `201` + `status=ready` if LLM is ready.
- `202` + `status=starting` if still warming up.

### 4.2 Proxy a Chat Request

```bash
LEASE_ID="<lease_id_from_previous_step>"

curl -sS -X POST http://192.168.8.36:8000/v1/proxy/v1/chat/completions \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Lease-Id: $LEASE_ID" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "llama3.1:8b",
    "messages": [{"role":"user","content":"Summarize leases in one sentence."}]
  }'
```

### 4.3 Streaming via Proxy

```bash
curl -N -X POST http://192.168.8.36:8000/v1/proxy/v1/chat/completions \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Lease-Id: $LEASE_ID" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "llama3.1:8b",
    "stream": true,
    "messages": [{"role":"user","content":"Write 3 short deployment checks."}]
  }'
```

### 4.4 Refresh and Release

```bash
# Extend TTL
curl -sS -X POST http://192.168.8.36:8000/v1/lease/$LEASE_ID/refresh \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"ttl_seconds":7200}'

# Release immediately
curl -sS -X POST http://192.168.8.36:8000/v1/lease/$LEASE_ID/release \
  -H "Authorization: Bearer $TOKEN"
```

## 5. Direct OpenAI-Compatible Usage

Use these when you do not need the generic proxy path.

### 5.1 List Models

```bash
curl -sS http://192.168.8.36:8000/v1/models
```

### 5.2 Chat Completions

```bash
curl -sS -X POST http://192.168.8.36:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "llama3.1:8b",
    "messages": [{"role":"user","content":"Hello"}],
    "temperature": 0.3,
    "top_p": 0.9,
    "max_tokens": 200
  }'
```

Notes:
- Supports `stream=true`.
- Supports `tools`/`tool_choice`.
- Legacy `functions`/`function_call` are translated for compatibility.
- Optional `system_prompt` is injected if no system-role message exists.

### 5.3 Completions (legacy clients, including FIM-style usage)

```bash
curl -sS -X POST http://192.168.8.36:8000/v1/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen2.5-coder:7b",
    "prompt": "def fibonacci(n):",
    "max_tokens": 120
  }'
```

### 5.4 Embeddings

```bash
curl -sS -X POST http://192.168.8.36:8000/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{
    "model": "nomic-embed-text:latest",
    "input": ["alpha", "beta"],
    "encoding_format": "float"
  }'
```

Notes:
- `input` supports string or array of strings.
- Max batch size defaults to `EMBEDDING_MAX_BATCH_SIZE=32`.
- `encoding_format` supports `float` and `base64`.
- Embedding responses are cached by model+input.

### 5.5 Images: Generations

```bash
curl -sS -X POST http://192.168.8.36:8000/v1/images/generations \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "cinematic cabin in snow",
    "size": "1024x1024",
    "n": 1,
    "steps": 20,
    "cfg_scale": 7,
    "response_format": "b64_json"
  }'
```

Notes:
- `response_format`: `b64_json` or `url`.
- `n` max defaults to `COMFYUI_MAX_BATCH_SIZE=4`.
- `model` can override default checkpoint.

### 5.6 Images: Edits (multipart)

```bash
curl -sS -X POST http://192.168.8.36:8000/v1/images/edits \
  -F "image=@input.png" \
  -F "mask=@mask.png" \
  -F "prompt=clean technical line-art style" \
  -F "denoise=0.35" \
  -F "n=2" \
  -F "response_format=b64_json"
```

JSON alternative:

```json
{
  "image_b64": "<base64>",
  "mask_b64": "<base64>",
  "prompt": "clean technical line-art style",
  "n": 2
}
```

### 5.7 Images: Variations

```bash
curl -sS -X POST http://192.168.8.36:8000/v1/images/variations \
  -F "image=@input.png" \
  -F "n=2" \
  -F "response_format=b64_json"
```

JSON alternative:

```json
{
  "image_b64": "<base64>",
  "n": 2
}
```

## 6. Health and Troubleshooting

Health:

```bash
curl -sS http://192.168.8.36:8000/v1/health \
  -H "Authorization: Bearer $TOKEN"
```

Common statuses:
- `401`: missing/invalid token (when token auth enabled).
- `403`: invalid/expired `X-Lease-Id`.
- `503`: upstream LLM not ready.
- `504`: proxy timeout to upstream.

Timeout guidance for OCR/vision:
- Set client request timeout to at least the agent's proxy timeout (`PROXY_UPSTREAM_TIMEOUT_SECONDS`, default `300`).
- For large page tiles or heavy models, increase `PROXY_UPSTREAM_TIMEOUT_SECONDS` and client timeout together.

## 7. Python Example (end-to-end)

```python
import requests

BASE = "http://192.168.8.36:8000"
TOKEN = "replace-with-token"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}

# 1) Create lease
lease = requests.post(
    f"{BASE}/v1/lease",
    headers={**HEADERS, "Content-Type": "application/json"},
    json={"client_id": "external-repo", "purpose": "chat", "ttl_seconds": 3600},
    timeout=180,
).json()

lease_id = lease["lease_id"]

# 2) Send request through proxy
resp = requests.post(
    f"{BASE}/v1/proxy/v1/chat/completions",
    headers={**HEADERS, "X-Lease-Id": lease_id, "Content-Type": "application/json"},
    json={
        "model": "llama3.1:8b",
        "messages": [{"role": "user", "content": "Say hello from llm-agent"}],
    },
    timeout=180,
)
print(resp.json())

# 3) Release
requests.post(f"{BASE}/v1/lease/{lease_id}/release", headers=HEADERS, timeout=30)
```
