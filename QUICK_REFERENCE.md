# Lease + Proxy API - Quick Reference Card

## ⚡ 30-Second Setup

```bash
# 1. Generate token
python3 setup_lease_api.py --generate-token

# 2. Export it
export LLM_AGENT_TOKEN="your-token-here"

# 3. Restart
sudo systemctl restart llm-agent-prod.service

# 4. Done! ✓
```

## 📡 API Quick Reference

### Create Lease
```bash
curl -X POST http://localhost:8000/v1/lease \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"client_id": "app", "purpose": "chat", "ttl_seconds": 3600}'
```
Returns: `lease_id`, `status` ("ready" or "starting")

### Get Lease Status
```bash
curl http://localhost:8000/v1/lease/$LEASE_ID \
  -H "Authorization: Bearer $TOKEN"
```

### Refresh Lease (extend TTL)
```bash
curl -X POST http://localhost:8000/v1/lease/$LEASE_ID/refresh \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"ttl_seconds": 7200}'
```

### Release Lease
```bash
curl -X POST http://localhost:8000/v1/lease/$LEASE_ID/release \
  -H "Authorization: Bearer $TOKEN"
```

### Health Check
```bash
curl http://localhost:8000/v1/health \
  -H "Authorization: Bearer $TOKEN"
```
Returns: `ok`, `vm_state`, `llm_ready`, `active_leases`

### Proxy Request
```bash
# Forward to LLM with lease ID
curl -X POST http://localhost:8000/v1/proxy/v1/chat/completions \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Lease-Id: $LEASE_ID" \
  -H "Content-Type: application/json" \
  -d '{"model": "llama2", "messages": [{"role": "user", "content": "Hi!"}]}'
```

## 🐍 Python Usage

```python
import requests

TOKEN = "your-token"
headers = {"Authorization": f"Bearer {TOKEN}"}

# Create lease
resp = requests.post(
    "http://localhost:8000/v1/lease",
    json={"client_id": "app", "purpose": "chat", "ttl_seconds": 3600},
    headers=headers,
)
lease_id = resp.json()["lease_id"]

# Use proxy
resp = requests.post(
    f"http://localhost:8000/v1/proxy/v1/chat/completions",
    json={"model": "llama2", "messages": [{"role": "user", "content": "Hi!"}]},
    headers={**headers, "X-Lease-Id": lease_id},
)
print(resp.json())

# Release
requests.post(
    f"http://localhost:8000/v1/lease/{lease_id}/release",
    headers=headers,
)
```

## 🔧 Configuration

| Env Var | Default | Purpose |
|---------|---------|---------|
| `LLM_AGENT_TOKEN` | (required) | Bearer token for auth |
| `LLM_BASE_URL` | `http://192.168.8.33:11434` | LLM server URL |
| `LLM_READINESS_PATH` | `/api/tags` | Readiness endpoint |
| `LEASE_DEFAULT_TTL` | `3600` | Default lease duration |
| `LLM_READINESS_TIMEOUT` | `120` | VM warmup timeout |
| `POWER_MODE` | `Medium` | Idle shutdown (Off/Medium/High) |

## 📝 Endpoints Overview

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/v1/lease` | ✓ | Create lease |
| GET | `/v1/lease/{id}` | ✓ | Get status |
| POST | `/v1/lease/{id}/refresh` | ✓ | Extend TTL |
| POST | `/v1/lease/{id}/release` | ✓ | Remove lease |
| GET | `/v1/health` | ✓ | Health check |
| * | `/v1/proxy/{path}` | ✓ | Proxy request |

## 🧪 Testing

```bash
# Unit tests (17 tests)
python3 test_lease.py

# Should output: OK
```

## 📚 Documentation Map

- **Quick Start**: This file
- **API Reference**: [LEASE_API_REFERENCE.md](LEASE_API_REFERENCE.md)
- **Implementation**: [LEASE_API_IMPLEMENTATION.md](LEASE_API_IMPLEMENTATION.md)
- **Deployment**: [DEPLOYMENT_CHECKLIST.md](DEPLOYMENT_CHECKLIST.md)
- **Summary**: [IMPLEMENTATION_SUMMARY.md](IMPLEMENTATION_SUMMARY.md)
- **Changes**: [CHANGES.md](CHANGES.md)

## 🐛 Troubleshooting

### 401 Unauthorized
```bash
# Check token
echo $LLM_AGENT_TOKEN

# Regenerate
python3 setup_lease_api.py --generate-token
```

### 503 LLM Not Ready
```bash
# Test readiness directly
curl http://192.168.8.33:11434/api/tags

# Check VM status
ssh proxmox "qm status 101"
```

### Lease Expires Too Quick
```bash
# Use longer TTL
curl -X POST http://localhost:8000/v1/lease \
  -d '{"ttl_seconds": 7200}' \
  -H "Authorization: Bearer $TOKEN"

# Or refresh periodically
curl -X POST http://localhost:8000/v1/lease/$ID/refresh \
  -d '{"ttl_seconds": 7200}' \
  -H "Authorization: Bearer $TOKEN"
```

## ✅ Verification

```bash
# 1. Syntax check
python3 -m py_compile lease.py auth.py lease_api.py

# 2. Import check
python3 -c "import lease, auth, lease_api; print('✓')"

# 3. Run tests
python3 test_lease.py

# 4. Test API
curl http://localhost:8000/v1/health \
  -H "Authorization: Bearer $TOKEN"
```

## 🔐 Security

- ✅ Always use Bearer token in `Authorization` header
- ✅ Store token securely (env var, secrets manager)
- ✅ Never commit token to git
- ✅ Rotate tokens periodically
- ✅ Use HTTPS in production
- ✅ Restrict IP access to llm-agent port

## 🚀 Key Features

✅ Time-limited leases with auto-expiry
✅ Automatic VM power-on when needed
✅ Readiness polling with exponential backoff
✅ HTTP proxy with streaming support
✅ Concurrent lease management
✅ Disk persistence (survives restarts)
✅ Activity tracking
✅ Comprehensive logging
✅ 100% backward compatible

## 📞 Support

For issues, check:
1. Logs: `journalctl -u llm-agent-prod -f`
2. Health: `curl http://localhost:8000/v1/health`
3. Documentation: See files listed above
4. Tests: `python3 test_lease.py -v`
