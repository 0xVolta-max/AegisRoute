# 🛠️ AegisRoute Operations & Runbook

## Daily Operations

### 1. Starting the Inference Bridge
```bash
# Automated headless launch
python3 cli/aegis.py start --url "https://colab.research.google.com/drive/YOUR_NOTEBOOK_ID"
```

### 2. Monitoring Active Status
```bash
# Check status and latency of inference endpoint
python3 cli/aegis.py status --url "https://your-tunnel.trycloudflare.com/v1"
```

### 3. Running Verification Tests
```bash
# Full test suite (Unit & Integration)
python3 -m unittest tests/test_unit_and_integration.py

# Function calling test
bash tests/test_tool_calling.sh

# Failover & circuit-breaker simulation
bash tests/test_failover.sh
```

## Emergency Procedures

### GPU Quota Reached (Exit Code 2)
When Colab free-tier GPU units are exhausted:
1. Playwright controller detects the quota dialog and exits with code 2.
2. AlertDispatcher sends a CRITICAL alert to macOS (`Basso` sound) and Discord/n8n.
3. OmniRoute Plugin automatically puts `colab-aegis` into a 4-hour cooldown.
4. All security audit and coding queries are redirected instantaneously to `local-mlx`, `anthropic`, or `openai`.
5. No manual action needed. Once cooldown elapses, the health checker probes for availability.

### Google Session Expiration
If Google logs out the session:
1. Run interactive authentication:
   ```bash
   python3 cli/aegis.py init-auth --url "https://colab.research.google.com/drive/YOUR_NOTEBOOK_ID"
   ```
2. Log into your Google account in the visible browser window.
3. Once logged in, close the window. The session in `colab_user_data/` is renewed.
