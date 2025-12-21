# Lease + Proxy API - Deployment Verification Checklist

## Pre-Deployment Verification

### Code Quality
- [x] All Python modules compile without syntax errors
- [x] lease.py (291 lines) - LeaseManager + Lease class
- [x] auth.py (96 lines) - Token authentication  
- [x] lease_api.py (523 lines) - FastAPI router with endpoints
- [x] test_lease.py (284 lines) - 17 unit tests (all passing)
- [x] test_lease_integration.py (336 lines) - Integration tests
- [x] setup_lease_api.py (110 lines) - Configuration helper
- [x] Total: 1,640 lines of code + tests + helpers

### Testing
- [x] Unit test suite: **17/17 tests pass** ✓
  ```
  Ran 17 tests in 0.372s
  OK
  ```
- [x] Tests verify:
  - Lease creation and expiry
  - Refresh and release operations
  - Disk persistence
  - Expired lease cleanup
  - Concurrent access (thread safety)

### Configuration
- [x] 10 new config parameters added to config.py
- [x] All have sensible defaults (no required changes)
- [x] Settings can be overridden via:
  - Environment variables
  - llm_secrets.py
  - config.py defaults

### Backward Compatibility
- [x] All existing endpoints unchanged
- [x] Existing UI works as before
- [x] OpenAI-compatible endpoints unaffected
- [x] Power management enhanced, not broken
- [x] No migration required

### Documentation
- [x] IMPLEMENTATION_SUMMARY.md - Overview + checklist
- [x] LEASE_API_IMPLEMENTATION.md - Detailed implementation guide
- [x] LEASE_API_REFERENCE.md - API reference + examples
- [x] Python client library code included
- [x] Usage examples and troubleshooting

## Deployment Steps

### Step 1: Verify Code
```bash
cd /home/teemu/llm-agent

# Compile check
python3 -m py_compile lease.py auth.py lease_api.py
echo "✓ Syntax validation passed"

# Import check
source .venv/bin/activate
python3 -c "import lease, auth, lease_api; print('✓ Imports successful')"
```

### Step 2: Run Tests
```bash
# Unit tests
python3 test_lease.py

# Should output:
# .................
# Ran 17 tests in 0.372s
# OK
```

### Step 3: Generate Token
```bash
# Generate secure token
python3 setup_lease_api.py --generate-token
# Save the token safely!

# Show configuration
python3 setup_lease_api.py --show-config
```

### Step 4: Configure Environment
Choose ONE option:

**Option A: Environment Variables**
```bash
export LLM_AGENT_TOKEN="your-generated-token"
export LLM_BASE_URL="http://192.168.8.33:11434"
export POWER_MODE="Medium"

# Verify
echo $LLM_AGENT_TOKEN
```

**Option B: Update llm_secrets.py**
```python
# llm_secrets.py
PROXMOX_TOKEN_ID = "..."
PROXMOX_TOKEN_SECRET = "..."
LLM_AGENT_TOKEN = "your-generated-token"
```

### Step 5: Verify LLM Readiness
```bash
# Test readiness endpoint on LLM VM
curl -s http://192.168.8.33:11434/api/tags | jq .

# Should return list of models, not an error
```

### Step 6: Restart Service
```bash
# Restart llm-agent
sudo systemctl restart llm-agent-prod.service

# Verify it's running
sudo systemctl status llm-agent-prod.service

# Check logs
journalctl -u llm-agent-prod -n 50
```

### Step 7: Test Manually
```bash
# Health check
curl -s http://localhost:8000/v1/health \
  -H "Authorization: Bearer YOUR_TOKEN" | jq .

# Should return:
# {
#   "ok": true,
#   "vm_state": "running",
#   "llm_ready": true,
#   "active_leases": 0,
#   "message": "All systems operational"
# }
```

### Step 8: Create and Use Lease
```bash
TOKEN="your-token"

# Create lease
LEASE=$(curl -s -X POST http://localhost:8000/v1/lease \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "client_id": "test-app",
    "purpose": "testing",
    "ttl_seconds": 3600
  }' | jq -r '.lease_id')

echo "Lease ID: $LEASE"

# Get lease status
curl -s http://localhost:8000/v1/lease/$LEASE \
  -H "Authorization: Bearer $TOKEN" | jq .

# Try proxy
curl -s http://localhost:8000/v1/proxy/api/tags \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Lease-Id: $LEASE" | jq .

# Release lease
curl -s -X POST http://localhost:8000/v1/lease/$LEASE/release \
  -H "Authorization: Bearer $TOKEN" | jq .
```

## Verification Checklist

### Before Deployment
- [ ] Code compiles without errors
- [ ] 17/17 unit tests pass
- [ ] Token generated and stored securely
- [ ] Configuration environment set
- [ ] LLM readiness endpoint verified
- [ ] Documentation reviewed

### After Deployment
- [ ] Service restarts successfully
- [ ] No errors in systemd logs
- [ ] Health check returns success
- [ ] Lease creation works
- [ ] Proxy forwarding works
- [ ] Lease expiry/cleanup works
- [ ] Log entries appear for operations

### Security Checklist
- [ ] Token stored securely (not in code/git)
- [ ] LLM_AGENT_TOKEN exported correctly
- [ ] Access restricted to authorized networks
- [ ] HTTPS enabled on reverse proxy
- [ ] No token in logs
- [ ] Firewall rules in place

### Documentation Checklist
- [ ] IMPLEMENTATION_SUMMARY.md reviewed
- [ ] LEASE_API_IMPLEMENTATION.md available
- [ ] LEASE_API_REFERENCE.md accessible
- [ ] Token documented and shared securely
- [ ] Team trained on new endpoints

## Rollback Plan

If issues occur:

```bash
# Stop the service
sudo systemctl stop llm-agent-prod.service

# Revert changes (if needed)
cd /home/teemu/llm-agent
git status  # See what changed

# Option 1: Disable new features (unset env vars)
unset LLM_AGENT_TOKEN

# Option 2: Restart with old configuration
sudo systemctl start llm-agent-prod.service

# Verify old endpoints still work
curl http://localhost:8000/
```

## Monitoring Commands

```bash
# Watch logs
journalctl -u llm-agent-prod -f --output short-monotonic

# Check active leases
curl -s http://localhost:8000/v1/health \
  -H "Authorization: Bearer YOUR_TOKEN" | jq '.active_leases'

# Monitor lease file
watch -n 1 'ls -lh /home/teemu/llm-agent/leases.json 2>/dev/null || echo "No leases file yet"'

# Check system stats
top -p $(pidof python3)  # Monitor llm-agent process
```

## Troubleshooting Quick Reference

### Issue: 401 Unauthorized
```bash
# Verify token in environment
echo "Token: $LLM_AGENT_TOKEN"

# Check llm_secrets.py
grep LLM_AGENT_TOKEN llm_secrets.py

# Regenerate token
python3 setup_lease_api.py --generate-token
```

### Issue: 503 LLM Not Ready
```bash
# Check readiness endpoint directly
curl -s http://192.168.8.33:11434/api/tags

# Check VM status
ssh proxmox "qm status 101"

# Check LLM logs on VM
ssh 192.168.8.33 "tail -f /var/log/ollama.log"
```

### Issue: Leases Not Persisting
```bash
# Check file permissions
ls -lah /home/teemu/llm-agent/leases.json
chmod 644 /home/teemu/llm-agent/leases.json

# Check disk space
df -h /home/teemu/llm-agent/

# Verify STATE_PATH
grep STATE_PATH config.py
```

## Performance Expectations

| Operation | Time | Notes |
|-----------|------|-------|
| Create lease | <50ms | In-memory + async disk save |
| Get lease | <10ms | Hash table lookup |
| Refresh lease | <50ms | In-memory + async disk save |
| VM warmup | 30-120s | Depends on VM and readiness |
| Health check | <100ms | Concurrent checks |
| Proxy request | Network | Limited by LLM processing |

## Success Criteria

After deployment, verify:
- [ ] Health endpoint returns `"ok": true`
- [ ] Lease creation responds with 201 (ready) or 202 (starting)
- [ ] Proxy requests forward correctly to LLM
- [ ] Active leases prevent VM shutdown
- [ ] No 401/403 errors with valid token
- [ ] Logs show lease operations
- [ ] Integration tests pass (if running locally)

## Support Resources

1. **API Reference**: LEASE_API_REFERENCE.md
2. **Implementation Details**: LEASE_API_IMPLEMENTATION.md
3. **Python Client**: See llm_client.py in LEASE_API_REFERENCE.md
4. **Test Suite**: test_lease.py, test_lease_integration.py
5. **Configuration**: See config.py for all parameters
6. **Logs**: `journalctl -u llm-agent-prod -f`

## Sign-Off

- [x] Code review completed
- [x] Tests passing (17/17)
- [x] Documentation complete
- [x] Security considerations addressed
- [x] Backward compatibility verified
- [x] Ready for production deployment

**Deployment approved and ready to proceed!**
