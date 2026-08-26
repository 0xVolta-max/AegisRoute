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
│             │                                │              │
│    ┌────────┴──────────┐                     │              │
│    │ NVMe Virtual Swap │ (14 GB Swap Space)  │              │
│    │ 3-Stage Cascade   │ (Golden Fallback)   │              │
│    └───────────────────┘                     │              │
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
- Automatically initializes a **14 GB NVMe Virtual Memory Swap** file on `/content` to prevent Host OOM kills on 27B/32B parameter models.
- Features an autonomous **3-Stage Self-Healing Cascade** (Primary Configuration -> Conservative Mitigation -> Stage 3 Golden Fallback `Qwen2.5-Coder-7B`).
- Dynamically allocates GPU layers (`compute_safe_gpu_layers`) based on active VRAM via `nvidia-smi`.
- Uses `llama-cpp-python[server]` with ChatML / OpenAI function calling schema support and native embedded GGUF Jinja2 chat templates.
- Employs unbuffered rolling logging (`SERVER_LOGS`) to capture instantaneous crash tracebacks.
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
- Directs matching requests to the specialized `0xalpha` security audit model or `Qwen3.8-Uncensored`.
- Automatically triggers failover to fallback chains (`local-mlx`, `anthropic`, `openai`) when Colab enters cooldown or becomes unreachable.
- Exposes administrative hot-reload webhook (`POST /aegis/update-tunnel`).

### 4. Alerting Subsystem (`core/alerting.py`)
- Native macOS alerts via `osascript` with audio cues (`Glass` on recovery, `Basso` on quota failure).
- Discord webhooks with color-coded rich embeds.
- n8n workflow integration via webhook payloads.

