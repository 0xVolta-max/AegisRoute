#@title 🛡️ AegisRoute - Autonomous Colab-LLM Inference Bridge { display-mode: "form" }
#@markdown Run high-performance specialized open-weights security and coding models on Google Colab with OpenAI-compatible API & Cloudflare Tunnel.

from __future__ import annotations

# ==============================================================================
# 🎛️ COLAB INTERACTIVE FORM CONTROLS
# ==============================================================================
#@markdown ### 🔑 1. Hugging Face Authentication (Erforderlich für private/gated Repositories)
#@markdown *Trage hier deinen Hugging Face Read-Token (`hf_...`) ein, falls du auf geschützte Modelle wie `0xalpha/Security-Audit-7B-GGUF` zugreifen möchtest.*
HF_TOKEN = "" #@param {type:"string"}

#@markdown ### 🧠 2. Modell-Auswahl & Hardware-Empfehlungen
#@markdown * 👑 **Qwen 3.8 Uncensored 27B (Heretic Abliterated - 0 Refusals):**
#@markdown   * `JonathanColetti/Qwen3.8-27B-Uncensored-GGUF` (Standard: `Qwen3.8-27B-Uncensored-noMTP-Q4_K_M.gguf`)
#@markdown   * *Free-Tier-Support:* Automatische NVMe-Swap-Erweiterung (12 GB) wird aktiviert, um OOM-Abstürze beim Laden zu verhindern.
#@markdown * 🟢 **Kompakte 7B/14B Uncensored Alternativen:**
#@markdown   * `bartowski/Qwen2.5-Coder-7B-Instruct-abliterated-GGUF` (100% GPU, ~50 Tok/s, Null OOM-Risiko)
#@markdown   * `bartowski/Qwen2.5-Coder-14B-Instruct-abliterated-GGUF` (14B High Reasoning)
#@markdown   * `0xalpha/Security-Audit-7B-GGUF` (Spezialisiert auf Security Audits & Smart Contracts)
MODEL_REPO = "JonathanColetti/Qwen3.8-27B-Uncensored-GGUF" #@param ["JonathanColetti/Qwen3.8-27B-Uncensored-GGUF", "bartowski/Qwen2.5-Coder-7B-Instruct-abliterated-GGUF", "bartowski/Qwen2.5-Coder-14B-Instruct-abliterated-GGUF", "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF", "0xalpha/Security-Audit-7B-GGUF", "bartowski/Qwen2.5-Coder-32B-Instruct-GGUF", "TheBloke/deepseek-coder-6.7B-instruct-GGUF"] {allow-input: true}
MODEL_FILE = "Qwen3.8-27B-Uncensored-noMTP-Q4_K_M.gguf" #@param ["Qwen3.8-27B-Uncensored-noMTP-Q4_K_M.gguf", "Qwen3.8-27B-Uncensored-noMTP-IQ4_XS.gguf", "Qwen3.8-27B-Uncensored-noMTP-IQ3_XXS.gguf", "Qwen3.8-27B-Uncensored-noMTP-Q2_K.gguf", "Qwen2.5-Coder-7B-Instruct-abliterated-Q4_K_M.gguf", "Qwen2.5-Coder-14B-Instruct-abliterated-Q4_K_M.gguf", "qwen2.5-coder-7b-instruct-q4_k_m.gguf", "0xalpha-Security-Audit-7B.Q4_K_M.gguf"] {allow-input: true}

#@markdown ### ⚙️ 3. Runtime & Hardware Einstellungen
#@markdown * **GPU_LAYERS:** `-1` für automatisches Offloading aller Layer auf T4/A100 GPU (bzw. adaptive Drosselung bei 27B/32B Modellen).
CONTEXT_WINDOW = 8192 #@param {type:"integer"}
PORT = 8000 #@param {type:"integer"}
GPU_LAYERS = -1 #@param {type:"integer"}
CHAT_FORMAT = "chatml" #@param ["chatml", "auto", "qwen-2", "llama-3"] {allow-input: true}


import collections
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Deque, Dict, List, Optional, Tuple

# Rolling diagnostic buffer for llama_cpp.server stderr/stdout
SERVER_LOGS: Deque[str] = collections.deque(maxlen=150)


def log(msg: str, level: str = "INFO") -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] [AegisBootstrap] {msg}", flush=True)


def run_command(cmd: str, env: Optional[dict] = None) -> int:
    log(f"Executing: {cmd}")
    process = subprocess.Popen(
        cmd,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env or os.environ.copy(),
    )
    for line in process.stdout:  # type: ignore
        if line.strip():
            print(f"  {line.rstrip()}", flush=True)
    process.wait()
    if process.returncode != 0:
        log(f"Command failed with code {process.returncode}: {cmd}", "ERROR")
    return process.returncode


def get_gpu_info() -> Tuple[bool, str, int]:
    """Check GPU availability and return (has_gpu, description, total_vram_mb)."""
    try:
        res = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader,nounits"],
            text=True,
        )
        parts = [p.strip() for p in res.strip().split(",")]
        gpu_name = parts[0] if len(parts) > 0 else "NVIDIA GPU"
        total_vram = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 15000
        free_vram = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else total_vram
        return True, f"{gpu_name} (Total: {total_vram}MB, Free: {free_vram}MB)", total_vram
    except Exception:
        return False, "No NVIDIA GPU detected", 0


def resolve_hf_token() -> Optional[str]:
    """Resolve Hugging Face Token from Form, Env, or Colab Secrets."""
    token = HF_TOKEN.strip() if 'HF_TOKEN' in globals() and HF_TOKEN else ""
    if not token:
        token = os.getenv("HF_TOKEN", "")
    if not token:
        try:
            from google.colab import userdata
            token = userdata.get('HF_TOKEN') or ""
        except Exception:
            pass
    return token.strip() if token else None


def configure_swap_memory(swap_size_gb: int = 12) -> None:
    """Configure virtual memory swap space on NVMe disk to prevent OOM killer on 27B models."""
    if os.path.exists("/swapfile"):
        return
    try:
        log(f"Configuring {swap_size_gb}GB virtual memory swap on NVMe disk to support 27B Qwen3.8...", "INFO")
        cmd = (
            f"fallocate -l {swap_size_gb}G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count={swap_size_gb * 1024} status=none && "
            "chmod 600 /swapfile && mkswap /swapfile >/dev/null 2>&1 && swapon /swapfile >/dev/null 2>&1"
        )
        subprocess.run(cmd, shell=True, timeout=40)
        log("Virtual memory swap active: Host addressable memory expanded successfully.", "INFO")
    except Exception as e:
        log(f"Swap configuration notice: {e}", "WARN")


def install_cloudflared() -> None:
    if shutil.which("cloudflared"):
        log("cloudflared is already installed.")
        return

    log("Downloading and installing cloudflared...")
    deb_url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb"
    cmd = (
        f"wget -q -O /tmp/cloudflared.deb {deb_url} && "
        "dpkg -i /tmp/cloudflared.deb > /dev/null 2>&1 || "
        "apt-get install -f -y > /dev/null 2>&1"
    )
    run_command(cmd)


def get_system_ram_mb() -> int:
    """Get total system RAM in MB."""
    try:
        with open("/proc/meminfo", "r") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) // 1024
    except Exception:
        pass
    return 12000


def install_dependencies(has_gpu: bool) -> None:
    log("Installing Python server dependencies (pydantic-settings, fastapi, uvicorn, sse-starlette, jinja2)...")
    run_command(
        "pip install -q --upgrade pip huggingface_hub jsonschema jinja2 "
        "pydantic pydantic-settings fastapi uvicorn sse-starlette starlette starlette-context httpx"
    )

    if has_gpu:
        log("Installing llama-cpp-python with CUDA acceleration...")
        cuda_env = os.environ.copy()
        cuda_env["CMAKE_ARGS"] = "-DGGML_CUDA=on"
        cuda_env["FORCE_CMAKE"] = "1"
        
        install_cmd = (
            "pip install -q llama-cpp-python[server] "
            "--extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124 "
            "--extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu123 "
            "--extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu122 "
            "--extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu121 "
            "|| pip install -q llama-cpp-python[server]"
        )
        run_command(install_cmd, env=cuda_env)
    else:
        log("No GPU detected. Installing CPU-optimized llama-cpp-python...")
        run_command("pip install -q llama-cpp-python[server]")


def download_model(repo_id: str, filename: str, token: Optional[str] = None) -> str:
    log(f"Downloading model {filename} from HuggingFace repo: {repo_id}...")
    from huggingface_hub import hf_hub_download

    if token:
        log("🔑 Authenticating with provided Hugging Face token...")
        try:
            from huggingface_hub import login
            login(token=token, add_to_git_credential=False)
        except Exception as exc:
            log(f"Notice on token login: {exc}", "WARN")

    os.makedirs("/content/models", exist_ok=True)

    try:
        model_path = hf_hub_download(
            repo_id=repo_id,
            filename=filename,
            local_dir="/content/models",
            token=token,
        )
        log(f"Model successfully downloaded to: {model_path}")
        return model_path
    except Exception as exc:
        err_msg = str(exc)
        log(f"Download failed for '{repo_id}/{filename}': {err_msg}", "ERROR")

        is_auth_or_gated = any(kw in err_msg.lower() for kw in [
            "401", "403", "unauthorized", "forbidden", "gated", "gatedrepoerror", "repositorynotfounderror", "restricted", "entrynotfound"
        ])

        # Automatic fallback to public unrestricted model
        if is_auth_or_gated and repo_id != "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF":
            print("\n" + "!" * 75)
            log(f"⚠️ Access restriction or missing file on '{repo_id}/{filename}'.", "WARN")
            log("🔄 Activating automatic fallback to public unrestricted SOTA model: Qwen/Qwen2.5-Coder-7B-Instruct-GGUF ...", "INFO")
            print("!" * 75 + "\n")

            fallback_repo = "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF"
            fallback_file = "qwen2.5-coder-7b-instruct-q4_k_m.gguf"
            return download_model(fallback_repo, fallback_file, token=None)
        raise exc


def build_server_command(
    model_path: str,
    port: int,
    gpu_layers: int,
    ctx_window: int,
    chat_format: str,
) -> List[str]:
    """Construct command line arguments for llama_cpp.server safely."""
    cmd = [
        sys.executable, "-u", "-m", "llama_cpp.server",
        "--model", model_path,
        "--n_gpu_layers", str(gpu_layers),
        "--n_ctx", str(ctx_window),
        "--host", "0.0.0.0",
        "--port", str(port),
    ]

    # Sanitize chat format
    fmt = (chat_format or "").strip().lower()
    if fmt == "chatml-function-calling":
        fmt = "chatml"  # Map legacy naming to valid format

    valid_formats = {"chatml", "qwen-2", "llama-3", "mistral-instruct", "hermes-2-pro"}
    if fmt in valid_formats:
        cmd.extend(["--chat_format", fmt])
    # If "auto" or unrecognized, omit --chat_format so llama_cpp uses embedded GGUF Jinja template

    return cmd


def start_llama_server(
    model_path: str,
    gpu_layers: int = -1,
    port: int = 8000,
    ctx_window: int = 8192,
    chat_format: str = "chatml",
) -> subprocess.Popen:
    SERVER_LOGS.clear()
    cmd = build_server_command(model_path, port, gpu_layers, ctx_window, chat_format)
    log(f"Starting llama_cpp.server on port {port} (layers={gpu_layers}, ctx={ctx_window}, format={chat_format})...")

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
    )

    def pipe_reader():
        for line in iter(proc.stdout.readline, ''):  # type: ignore
            if line:
                cleaned = line.rstrip()
                SERVER_LOGS.append(cleaned)
                print(f"[llama.cpp] {cleaned}", flush=True)

    t = threading.Thread(target=pipe_reader, daemon=True)
    t.start()
    return proc


def dump_server_diagnostics() -> None:
    """Print captured server logs on startup failure."""
    print("\n" + "=" * 70)
    print(" 🛑 LLAMA_CPP.SERVER CRASH DIAGNOSTIC TRACEBACK")
    print("=" * 70)
    if not SERVER_LOGS:
        print("  (No output lines captured from server process)")
    else:
        for l in SERVER_LOGS:
            print(f"  {l}")
    print("=" * 70 + "\n")


def start_cloudflare_tunnel(port: int = 8000) -> Tuple[subprocess.Popen, str]:
    log(f"Starting Cloudflare Quick Tunnel for port {port}...")
    cmd = ["cloudflared", "tunnel", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate"]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    tunnel_url = ""
    url_pattern = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")

    # Cloudflare outputs the tunnel URL to stderr
    start_wait = time.time()
    while time.time() - start_wait < 60:
        line = proc.stderr.readline()  # type: ignore
        if line:
            print(f"[cloudflared] {line.rstrip()}", flush=True)
            match = url_pattern.search(line)
            if match:
                tunnel_url = match.group(0)
                break
        time.sleep(0.1)

    if not tunnel_url:
        log("Failed to acquire Cloudflare tunnel URL within timeout!", "ERROR")
        raise RuntimeError("Cloudflare tunnel failed to start.")

    return proc, tunnel_url


def wait_for_server_ready(port: int, proc: subprocess.Popen, timeout: int = 180) -> bool:
    """Poll local llama_cpp server until /v1/models returns HTTP 200."""
    import urllib.request
    import json

    log(f"Waiting up to {timeout}s for llama_cpp.server to become healthy on port {port}...")
    start_t = time.time()
    url = f"http://127.0.0.1:{port}/v1/models"

    while time.time() - start_t < timeout:
        # Check if server process terminated prematurely
        ret = proc.poll()
        if ret is not None:
            log(f"llama_cpp.server process exited unexpectedly with return code {ret}!", "ERROR")
            dump_server_diagnostics()
            return False

        try:
            req = urllib.request.Request(url, headers={"User-Agent": "AegisHealthCheck/1.0"})
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                if resp.status == 200:
                    body = resp.read().decode("utf-8")
                    data = json.loads(body)
                    models = [m.get("id") for m in data.get("data", [])]
                    log(f"Server is HEALTHY and listening on port {port}! Loaded models: {models}", "INFO")
                    return True
        except Exception:
            pass

        time.sleep(2)

    log(f"Timeout ({timeout}s) exceeded while waiting for llama_cpp.server to respond!", "ERROR")
    dump_server_diagnostics()
    return False


def verify_tunnel_connectivity(tunnel_url: str, timeout: int = 45) -> bool:
    """Verify end-to-end Cloudflare tunnel ingress before broadcasting readiness."""
    import urllib.request

    log(f"Verifying end-to-end tunnel ingress at {tunnel_url}...")
    start_t = time.time()
    url = f"{tunnel_url.rstrip('/')}/v1/models"

    while time.time() - start_t < timeout:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "AegisHealthCheck/1.0"})
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                if resp.status == 200:
                    log("End-to-end Cloudflare tunnel ingress verified successfully (HTTP 200)!", "INFO")
                    return True
        except Exception:
            pass
        time.sleep(3)

    log("Tunnel verification warning: Upstream edge response pending.", "WARN")
    return False


def compute_safe_gpu_layers(
    has_gpu: bool,
    total_vram_mb: int,
    model_name: str,
    requested_layers: int,
) -> int:
    """Dynamically determine safe GPU layer count to prevent CUDA OOM."""
    if not has_gpu:
        return 0
    if requested_layers != -1:
        return requested_layers

    name_lower = model_name.lower()
    if any(k in name_lower for k in ["70b", "72b"]):
        return 8 if total_vram_mb >= 14000 else 0
    if any(k in name_lower for k in ["27b", "32b"]):
        # Safe limit for 15GB T4 with 8k context KV cache
        return 18 if total_vram_mb >= 14000 else 10
    if any(k in name_lower for k in ["14b", "13b"]):
        return 32 if total_vram_mb >= 14000 else 16

    # 7B / 8B / 3B / 1.5B fits 100% on T4 GPU
    return -1


def main():
    print("=" * 70)
    print(" 🛡️  AegisRoute Colab Autonomous LLM Inference Bridge")
    print("=" * 70)

    has_gpu, gpu_status, total_vram_mb = get_gpu_info()
    sys_ram_mb = get_system_ram_mb()
    log(f"Hardware Status: {gpu_status} | System RAM: {sys_ram_mb}MB")

    if not has_gpu:
        print("\n" + "⚠️ " * 30)
        log("HINWEIS: Es wurde keine NVIDIA GPU erkannt!", "WARN")
        log("Für maximale Geschwindigkeit stelle bitte den Colab-Typ um:", "WARN")
        log("Menü: Runtime -> Change runtime type -> T4 GPU (oder A100)", "WARN")
        print("⚠️ " * 30 + "\n")

    install_cloudflared()
    install_dependencies(has_gpu)

    token = resolve_hf_token()
    model_repo = MODEL_REPO.strip() if 'MODEL_REPO' in globals() else os.getenv("AEGIS_MODEL_REPO", "JonathanColetti/Qwen3.8-27B-Uncensored-GGUF")
    model_file = MODEL_FILE.strip() if 'MODEL_FILE' in globals() else os.getenv("AEGIS_MODEL_FILE", "Qwen3.8-27B-Uncensored-noMTP-Q4_K_M.gguf")

    # Auto-correct Qwen3.8 MTP variants: llama.cpp requires the noMTP build
    if "qwen3.8" in (model_repo + model_file).lower() and "nomtp" not in model_file.lower():
        if "q4_k_m" in model_file.lower():
            log(f"Auto-correcting model file '{model_file}' -> 'Qwen3.8-27B-Uncensored-noMTP-Q4_K_M.gguf' (noMTP-Build erforderlich für llama.cpp).", "INFO")
            model_file = "Qwen3.8-27B-Uncensored-noMTP-Q4_K_M.gguf"

    # Check if selected model exceeds Colab Free Tier hardware capabilities
    is_large_model = any(k in (model_repo + model_file).lower() for k in ["27b", "32b", "70b"])
    if is_large_model and sys_ram_mb < 20000:
        log(f"Hinweis: '{model_repo}' ist ein 27B+ Modell (~17GB).", "INFO")
        configure_swap_memory(swap_size_gb=14)

    model_path = download_model(model_repo, model_file, token=token)

    # Compute optimal safe layer offloading
    effective_gpu_layers = compute_safe_gpu_layers(
        has_gpu, total_vram_mb, f"{model_repo}/{model_file}", GPU_LAYERS
    )

    # --- STAGE 1: Primary Server Launch ---
    server_proc = start_llama_server(
        model_path,
        gpu_layers=effective_gpu_layers,
        port=PORT,
        ctx_window=CONTEXT_WINDOW,
        chat_format=CHAT_FORMAT,
    )

    # --- STAGE 2: Conservative Recovery (Same Model, Reduced Footprint) ---
    if not wait_for_server_ready(PORT, server_proc, timeout=90):
        log("Stage 1 server launch failed. Attempting Stage 2 Self-Healing (Reduced GPU layers & 4k context)...", "WARN")
        try:
            server_proc.terminate()
            server_proc.wait(timeout=5)
        except Exception:
            pass

        recovery_layers = max(0, effective_gpu_layers // 2) if effective_gpu_layers > 0 else 0
        log(f"Stage 2: Retrying with layers={recovery_layers}, ctx=4096, format='auto'...", "INFO")
        server_proc = start_llama_server(
            model_path,
            gpu_layers=recovery_layers,
            port=PORT,
            ctx_window=min(CONTEXT_WINDOW, 4096),
            chat_format="auto",
        )

        # --- STAGE 3: Golden Fallback (Universal SOTA 7B Model) ---
        if not wait_for_server_ready(PORT, server_proc, timeout=60):
            print("\n" + "!" * 75)
            log(f"🛑 Model '{model_repo}/{model_file}' could not be stabilized on this hardware tier.", "WARN")
            log("🔄 Activating Stage 3 Golden Fallback: Loading guaranteed SOTA model 'Qwen/Qwen2.5-Coder-7B-Instruct-GGUF'...", "INFO")
            print("!" * 75 + "\n")
            try:
                server_proc.terminate()
                server_proc.wait(timeout=5)
            except Exception:
                pass

            golden_repo = "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF"
            golden_file = "qwen2.5-coder-7b-instruct-q4_k_m.gguf"
            golden_path = download_model(golden_repo, golden_file, token=None)

            golden_layers = -1 if has_gpu else 0
            server_proc = start_llama_server(
                golden_path,
                gpu_layers=golden_layers,
                port=PORT,
                ctx_window=CONTEXT_WINDOW,
                chat_format="auto",
            )

            if not wait_for_server_ready(PORT, server_proc, timeout=120):
                log("Fatal error: llama_cpp.server failed to initialize after 3-stage cascade. Aborting bootstrap.", "ERROR")
                server_proc.terminate()
                return

    tunnel_proc, raw_tunnel_url = start_cloudflare_tunnel(PORT)
    api_base_url = f"{raw_tunnel_url.rstrip('/')}/v1"

    # Verify end-to-end tunnel ingress
    verify_tunnel_connectivity(raw_tunnel_url)

    print("\n" + "=" * 70)
    print(f" [AEGIS_READY] BASE_URL={api_base_url}")
    print("=" * 70 + "\n")

    log("AegisRoute is now operational and awaiting requests...")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log("Shutting down services gracefully...")
        tunnel_proc.terminate()
        server_proc.terminate()


if __name__ == "__main__":
    main()


