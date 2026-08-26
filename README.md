# 🛡️ AegisRoute

<div align="center">

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![OmniRoute Plugin](https://img.shields.io/badge/OmniRoute-Compatible%20Plugin-success.svg)](https://github.com/omniroute/omniroute)
[![Strix Scanner](https://img.shields.io/badge/Strix-Security%20Audit%20Ready-orange.svg)](https://github.com/strix-security)
[![Playwright](https://img.shields.io/badge/Automated-Playwright%20Engine-green.svg)](https://playwright.dev/)

**Autonomous Google Colab GPU-LLM Inference Bridge & DevSecOps Smart Router Plugin for OmniRoute**

Host high-performance open-weights models (0xalpha Security-Audit, Qwen 2.5/3.8) free of charge on Google Colab, automate startup & quota management, and orchestrate zero-latency fallback routing for automated DevSecOps CI/CD security audits.

</div>

---

## 🏛️ System Architecture

```text
               ┌─────────────────────────────────────────────────────────┐
               │                   GOOGLE COLAB RUNTIME                  │
               │  ┌──────────────────────┐    ┌────────────────────────┐  │
               │  │  llama_cpp.server    │    │  cloudflared tunnel    │  │
               │  │  (0xalpha / Qwen)    │◄───┤  (HTTPS Quick Tunnel)  │  │
               │  │  CUDA • ChatML Tools │    └───────────┬────────────┘  │
               └──┴──────────▲───────────┴────────────────┼───────────────┘
                             │                            │
                     Control │ Shortcut                   │ *.trycloudflare.com/v1
                             │ (Ctrl+F9)                  │
               ┌─────────────┴───────────┐                │
               │  Headless Playwright    │                │
               │  Controller & Watcher   │                │
               │  (./colab_user_data)    │                │
               └─────────────┬───────────┘                │
                             │ Exit Code 0/1/2            │
                             ▼                            ▼
               ┌─────────────────────────────────────────────────────────┐
               │               OMNIRoute LOCAL AI PROXY                 │
               │                                                         │
               │   ┌─────────────────────────────────────────────────┐   │
               │   │           AegisRoute Plugin (index.js)          │   │
               │   │  • Task Router (Security Regex -> 0xalpha)      │   │
               │   │  • Health Watcher (Ping /v1/models every 30s)   │   │
               │   │  • Circuit Breaker (4h Cooldown on Quota Exceed)│   │
               │   │  • Dynamic Webhook (POST /aegis/update-tunnel)  │   │
               │   └────────────────────────┬────────────────────────┘   │
               │                            │                            │
               │             ┌──────────────┴─────────────┐              │
               │             ▼                            ▼              │
               │     [colab-aegis] (Primary)      [Fallback Chain]       │
               │     0xalpha Security Model       (MLX / Anthropic)      │
               └────────────────────▲────────────────────────────────────┘
                                    │ OpenAI API (http://localhost:20128/v1)
               ┌────────────────────┴────────────────────────────────────┐
               │                      CLIENT LAYER                       │
               │  ┌──────────────────────┐    ┌────────────────────────┐  │
               │  │ Strix Security Scan  │    │ Developer Tools        │  │
               │  │ (GitHub Actions PRs) │    │ (Claude / Cursor / IDE)│  │
               └──┴──────────────────────┴────┴────────────────────────┘
```

---

## ✨ Key Features

- 🆓 **Zero-Cost Specialized Inference**: Run SOTA models like `0xalpha/Security-Audit-7B` or `Qwen/Qwen2.5-Coder-32B` on free Google Colab T4/A100 instances.
- 🤖 **Autonomous Playwright Controller**: Headless browser automation that loads Colab, bypasses anti-bot heuristics, triggers execution shortcuts, and scrapes live tunnel endpoints.
- ⚡ **OmniRoute Smart Plugin**: Seamlessly registers the `colab-aegis` provider with OpenAI-compatible function calling, task-based keyword routing, and live endpoint updates.
- 🛡️ **Intelligent Circuit Breaker**: Detects GPU quota modal dialogs (`Exit Code 2`), places the instance into a 4-hour cooldown, and redirects traffic instantaneously to backup providers (`local-mlx`, `anthropic`, `openai`).
- 🚨 **Multi-Channel Alerting**: Instant notifications across native macOS notifications (with sound triggers `Basso`/`Glass`), Discord rich embeds, and n8n webhooks.
- 🔍 **Strix CI/CD Integration**: Out-of-the-box GitHub Actions workflow that executes AI-powered security audits on Pull Requests and posts findings directly to GitHub comments.

---

## 🚀 5-Minute Quickstart

### 1. Prerequisites
- Python 3.10+
- Node.js 18+
- Google Account with access to Google Colab

### 2. Installation
```bash
git clone https://github.com/your-org/AegisRoute.git
cd AegisRoute

# Set up Python environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

# Set up OmniRoute Plugin
cd plugin
npm install
cd ..

# Configure environment variables
cp config/aegis.env.example .env
```

### 3. One-Time Google Authentication
Authenticate your Google account once to preserve persistent browser cookies:
```bash
python3 cli/aegis.py init-auth --url "https://colab.research.google.com/drive/YOUR_NOTEBOOK_ID"
```
*A visible Chromium window opens. Log into your Google account. The session will be safely stored in `./colab_user_data`.*

### 4. Create the Google Colab Notebook
1. Open [Google Colab](https://colab.research.google.com) and create a new notebook.
2. Select **Runtime** > **Change runtime type** > **T4 GPU** (or A100).
3. Copy the entire contents of [`colab/runtime_bootstrap.py`](file:///colab/runtime_bootstrap.py) into the first cell.
4. Save the notebook and copy its URL into your `.env` as `AEGIS_COLAB_URL`.

### 5. Launch AegisRoute
```bash
# Start Colab headless and sync tunnel with OmniRoute
python3 cli/aegis.py start

# Check active status & model health
python3 cli/aegis.py status
```

---

## 🔌 OmniRoute Plugin Integration

AegisRoute provides a native provider & router plugin for [OmniRoute](https://github.com/omniroute/omniroute).

Add the plugin to your OmniRoute configuration:

```json
{
  "plugins": {
    "omniroute-plugin-aegis": {
      "enabled": true,
      "path": "./plugin",
      "tunnelUrl": "http://localhost:8000/v1",
      "cooldownHours": 4.0,
      "fallbackChain": ["local-mlx", "anthropic", "openai"],
      "securityKeywords": ["audit", "vulnerability", "reentrancy", "exploit", "cve", "smart contract"]
    }
  }
}
```

### Dynamic Hot-Update Endpoint
When Colab reboots, the Playwright controller or your webhook can notify OmniRoute immediately without restarting the proxy:
```bash
curl -X POST http://localhost:20128/aegis/update-tunnel \
  -H "Content-Type: application/json" \
  -d '{"tunnel_url": "https://random-words.trycloudflare.com/v1"}'
```

---

## 🔒 Automated Strix PR Audits (CI/CD)

The included workflow [`.github/workflows/strix-audit.yml`](file:///.github/workflows/strix-audit.yml) automatically scans Pull Requests modifying `contracts/**` or `src/**`:

```yaml
# Add to your GitHub Secrets:
OPENAI_BASE_URL: "https://your-omniroute-proxy.domain/v1"
OPENAI_API_KEY: "your-omniroute-key"
```

When a PR is opened, the specialized `0xalpha/Security-Audit-7B` model analyzes code diffs for:
- Reentrancy attacks & state updates after external calls
- Access control flaws & privileged role escalation
- Flash loan oracle vulnerabilities & unchecked return values
- Injection and data sanitization vulnerabilities

---

## 🧪 Testing Suite

Validate tool-calling and failover mechanisms:

```bash
# Test OpenAI-compatible function calling (tools schema)
chmod +x tests/test_tool_calling.sh
./tests/test_tool_calling.sh

# Simulate Colab GPU quota exhaustion & circuit-breaker activation
chmod +x tests/test_failover.sh
./tests/test_failover.sh
```

---

## 🔧 Troubleshooting

| Issue | Cause | Solution |
| :--- | :--- | :--- |
| **Exit Code 2 (Quota Exceeded)** | Google Colab free GPU compute units exhausted | Circuit-breaker automatically routes to fallback chain for 4 hours. No manual intervention required. |
| **Login Redirect in Headless** | Google session cookie expired | Run `python3 cli/aegis.py init-auth` to re-login interactively. |
| **Cloudflare Tunnel Delay** | Temporary latency in quick tunnel creation | Increase timeout via `python3 cli/aegis.py start --timeout 600`. |
| **VRAM Out-of-Memory** | Loading large models (32B) on T4 GPU | Ensure `runtime_bootstrap.py` uses `--n_gpu_layers 26` to offload layers to CPU RAM. |

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
