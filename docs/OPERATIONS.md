# 🛠️ AegisRoute Operations & Runbook

## Daily Operations

### 1. Starting the Inference Bridge
```bash
# Automated headless launch
python3 cli/aegis.py start --url "https://colab.research.google.com/drive/YOUR_NOTEBOOK_ID"
```

### 2. Monitoring Active Status & Live Probe
```bash
# Check status, roundtrip latency, and active loaded models
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

---

## 🧠 Model Selection & Hardware Tier Matrix

| Model Identifier | File / Quantization | Min. Hardware Tier | T4 GPU Layers | Throughput (Tokens/s) |
| :--- | :--- | :--- | :--- | :--- |
| **`JonathanColetti/Qwen3.8-27B-Uncensored-GGUF`** | `...noMTP-IQ4_XS.gguf` / `...noMTP-Q4_K_M.gguf` | Colab Free-Tier (mit NVMe Swap) oder Pro | 16–18 Layer | ~25–35 Tok/s (GPU) |
| **`bartowski/Qwen2.5-Coder-7B-Instruct-abliterated-GGUF`** | `Qwen2.5-Coder-7B-Instruct-abliterated-Q4_K_M.gguf` | Colab Free-Tier | -1 (100% GPU) | ~45–60 Tok/s |
| **`0xalpha/Security-Audit-7B-GGUF`** | `0xalpha-Security-Audit-7B.Q4_K_M.gguf` | Colab Free-Tier | -1 (100% GPU) | ~45–60 Tok/s |
| **`bartowski/Qwen2.5-Coder-14B-Instruct-abliterated-GGUF`** | `Qwen2.5-Coder-14B-Instruct-abliterated-Q4_K_M.gguf` | Colab Free-Tier | 32 Layer | ~30–40 Tok/s |

> [!IMPORTANT]
> **Qwen3.8 noMTP Requirement:**  
> Bei `JonathanColetti/Qwen3.8-27B` muss zwingend die **`noMTP`**-Variante verwendet werden (z. B. `Qwen3.8-27B-Uncensored-noMTP-IQ4_XS.gguf`), da standardmäßige `llama.cpp`-Engines MTP-Tensors (Multi-Token Prediction) nicht unterstützen. Der Bootstrap-Code korrigiert dies automatisch.

---

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

### `libcudart.so.12` / CPU Mode Warning
If `Hardware Status: No NVIDIA GPU detected` or `libcudart.so.12: cannot open shared object file` appears:
1. Ensure Colab Hardware Accelerator is set to **T4 GPU** or **A100** (`Runtime -> Change runtime type`).
2. The bootstrap script automatically installs `nvidia-cuda-runtime-cu12` and injects `LD_LIBRARY_PATH` dynamically.

### 3-Stage Self-Healing Cascade
If the primary server configuration fails (e.g. VRAM overallocation):
* **Stage 1:** Primärer Start mit dynamischen GPU-Layern.
* **Stage 2:** Lokale Drosselung (4096 Kontext, native Jinja-Templates, 50% Layer).
* **Stage 3 (Golden Fallback):** Automatischer Download und Start des garantierten SOTA-Modells `Qwen/Qwen2.5-Coder-7B-Instruct-GGUF` (100% GPU Offload).

