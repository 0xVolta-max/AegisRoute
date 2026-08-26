# 🏛️ AegisRoute Architecture & Specifications

## System Topology & Flow

```text
┌─────────────────────────────────────────────────────────────┐
│                 GOOGLE COLAB RUNTIME (T4 GPU)               │
│  ┌──────────────────────┐        ┌───────────────────────┐  │
│  │  llama_cpp.server    │◄───────┤  cloudflared Tunnel   │  │
│  │  (Port 8000 / CUDA)  │ [HTTP] │  (Edge trycloudflare) │  │
│  └──────────▲───────────┘        └───────────▲───────────┘  │
│             │                                │              │
│    wait_for_server_ready()        verify_tunnel_connectivity()
└─────────────┼────────────────────────────────┼──────────────┘
              │ [AEGIS_READY] Marker           │ HTTPS Ingress
┌─────────────┴───────────┐                    ▼
│   Playwright Controller │      ┌───────────────────────────┐
│   (Stealth Automation)  │      │ OmniRoute Gateway Plugin  │
│   • Quota-Dialog Check  │      │ • Security Regex Routing  │
│   • Health Pre-Probe    │      │ • 4h Circuit Breaker      │
└─────────────────────────┘      │ • Zero-Latency Fallback   │
                                 └───────────────────────────┘
```

## Architectural Components

### 1. Colab Runtime Bootstrap (`colab/runtime_bootstrap.py`)
- Downloads quantized GGUF models directly to ephemeral Colab storage.
- Dynamically allocates GPU layers to avoid VRAM oversubscription.
- Uses `llama-cpp-python[server]` with ChatML / OpenAI function calling schema support.
- Employs a pre-flight probe (`wait_for_server_ready`) before Cloudflare Quick Tunnel exposure.
- Validates end-to-end edge ingress (`verify_tunnel_connectivity`) prior to printing `[AEGIS_READY]`.

### 2. Autonomous Headless Controller (`core/playwright_controller.py`)
- Runs Playwright in stealth mode with persistent session profile (`colab_user_data`).
- Injects anti-bot bypass flags (`--disable-blink-features=AutomationControlled`).
- Triggers notebook execution shortcuts (`Control+F9` / `Meta+F9`).
- Monitors modal dialogs for German and English GPU quota exhaustion text.
- Validates endpoint connectivity asynchronously before reporting exit code 0.

### 3. OmniRoute Smart Router Plugin (`plugin/index.js`)
- Dynamically registers the `colab-aegis` provider with OpenAI-compatible model catalog.
- Evaluates inbound user prompt tokens against pre-compiled security keywords (`audit`, `reentrancy`, `cve`, etc.).
- Directs matching requests to the specialized `0xalpha` security audit model.
- Automatically triggers failover to fallback chains (`local-mlx`, `anthropic`, `openai`) when Colab enters cooldown or becomes unreachable.
- Exposes administrative hot-reload webhook (`POST /aegis/update-tunnel`).

### 4. Alerting Subsystem (`core/alerting.py`)
- Native macOS alerts via `osascript` with audio cues (`Glass` on recovery, `Basso` on quota failure).
- Discord webhooks with color-coded rich embeds.
- n8n workflow integration via webhook payloads.
