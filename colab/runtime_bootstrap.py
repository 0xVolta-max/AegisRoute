#@title 🛡️ AegisRoute - Autonomous Colab-LLM Inference Bridge { display-mode: "form" }
#@markdown Run high-performance specialized open-weights security and coding models on Google Colab with OpenAI-compatible API & Cloudflare Tunnel.

from __future__ import annotations

# ==============================================================================
# 🎛️ COLAB INTERACTIVE FORM CONTROLS
# ==============================================================================
#@markdown ### 🔑 1. Hugging Face Authentication (Erforderlich für private/gated Repositories)
#@markdown *Trage hier deinen Hugging Face Read-Token (`hf_...`) ein, falls du auf geschützte Modelle wie `0xalpha/Security-Audit-7B-GGUF` zugreifen möchtest.*
HF_TOKEN = "" #@param {type:"string"}

#@markdown ### 🧠 2. Modell-Auswahl & Freie Eingabe (Custom Models)
#@markdown * **Vordefinierte Modelle:** Wähle eines der getesteten Modelle aus dem Dropdown-Menü.
#@markdown * **Freie Eingabe (Custom Model):** Du kannst direkt in die Textfelder klicken und ein beliebiges anderes Hugging Face Repository (`Organisation/Modell-Name`) sowie den gewünschten `.gguf`-Dateinamen eingeben.
#@markdown * **Hinweis zu Gated Models:** Manche Modelle (z. B. `orcarouter/...` oder `0xalpha/...`) sind auf Hugging Face geschützt. Schalte dort den Zugriff frei oder nutze das standardmäßige, sofort frei verfügbare `Qwen/Qwen2.5-Coder-7B-Instruct-GGUF`.
MODEL_REPO = "JonathanColetti/Qwen3.8-27B-Uncensored-GGUF" #@param ["JonathanColetti/Qwen3.8-27B-Uncensored-GGUF", "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF", "0xalpha/Security-Audit-7B-GGUF", "bartowski/Qwen2.5-Coder-7B-Instruct-GGUF", "bartowski/Qwen2.5-Coder-14B-Instruct-GGUF", "bartowski/Qwen2.5-Coder-32B-Instruct-GGUF", "TheBloke/deepseek-coder-6.7B-instruct-GGUF"] {allow-input: true}
MODEL_FILE = "Qwen3.8-27B-Uncensored-Q4_K_M.gguf" #@param ["Qwen3.8-27B-Uncensored-Q4_K_M.gguf", "Qwen3.8-27B-Uncensored-noMTP-Q4_K_M.gguf", "qwen2.5-coder-7b-instruct-q4_k_m.gguf", "model-q4_k_m.gguf", "Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf", "Qwen2.5-Coder-14B-Instruct-Q4_K_M.gguf", "Qwen2.5-Coder-32B-Instruct-Q4_K_M.gguf"] {allow-input: true}

#@markdown ### ⚙️ 3. Runtime & Hardware Einstellungen
#@markdown * **GPU_LAYERS:** `-1` für automatische Offloading-Berechnung (alle Layer auf T4 GPU, bzw. 26 Layer bei 27B/32B Modellen).
CONTEXT_WINDOW = 8192 #@param {type:"integer"}
PORT = 8000 #@param {type:"integer"}
GPU_LAYERS = -1 #@param {type:"integer"}
CHAT_FORMAT = "chatml-function-calling" #@param ["chatml-function-calling", "chatml", "qwen-2", "llama-3"] {allow-input: true}


import os
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Dict, Optional, Tuple


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


def get_gpu_info() -> Tuple[bool, str]:
    try:
        res = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader"],
            text=True,
        )
        return True, res.strip()
    except Exception:
        return False, "No NVIDIA GPU detected"


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


def install_dependencies(has_gpu: bool) -> None:
    log("Installing Python dependencies (huggingface_hub, jsonschema, jinja2)...")
    run_command("pip install -q --upgrade pip huggingface_hub jsonschema jinja2 pydantic fastapi uvicorn sse-starlette httpx")

    if has_gpu:
        log("Installing llama-cpp-python with CUDA support...")
        cuda_env = os.environ.copy()
        cuda_env["CMAKE_ARGS"] = "-DGGML_CUDA=on"
        cuda_env["FORCE_CMAKE"] = "1"
        
        install_cmd = (
            "pip install -q llama-cpp-python[server] "
            "--extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu122 || "
            "pip install -q llama-cpp-python[server] "
            "--extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu121 || "
            "pip install -q llama-cpp-python[server]"
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
            "401", "403", "unauthorized", "forbidden", "gated", "gatedrepoerror", "repositorynotfounderror", "restricted"
        ])

        # Automatic fallback to public unrestricted model
        if is_auth_or_gated and repo_id != "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF":
            print("\n" + "!" * 75)
            log(f"⚠️ Access restriction on '{repo_id}' (Gated / Restricted / Unauthorized).", "WARN")
            log(f"👉 To use this specific model, accept access terms at: https://huggingface.co/{repo_id}", "WARN")
            log("🔄 Activating automatic fallback to public unrestricted SOTA model: Qwen/Qwen2.5-Coder-7B-Instruct-GGUF ...", "INFO")
            print("!" * 75 + "\n")

            fallback_repo = "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF"
            fallback_file = "qwen2.5-coder-7b-instruct-q4_k_m.gguf"
            return download_model(fallback_repo, fallback_file, token=None)
        raise exc


def start_llama_server(model_path: str, gpu_layers: int = -1) -> subprocess.Popen:
    log(f"Starting llama_cpp.server on port {PORT} (layers={gpu_layers}, ctx={CONTEXT_WINDOW}, format={CHAT_FORMAT})...")
    cmd = [
        sys.executable, "-m", "llama_cpp.server",
        "--model", model_path,
        "--n_gpu_layers", str(gpu_layers),
        "--n_ctx", str(CONTEXT_WINDOW),
        "--chat_format", CHAT_FORMAT,
        "--host", "0.0.0.0",
        "--port", str(PORT),
    ]
    
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    def pipe_reader():
        for line in iter(proc.stdout.readline, ''):  # type: ignore
            if line:
                print(f"[llama.cpp] {line.rstrip()}", flush=True)

    t = threading.Thread(target=pipe_reader, daemon=True)
    t.start()
    return proc


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


def main():
    print("=" * 70)
    print(" 🛡️  AegisRoute Colab Autonomous LLM Inference Bridge")
    print("=" * 70)
    
    has_gpu, gpu_status = get_gpu_info()
    log(f"Hardware Status: {gpu_status}")

    if not has_gpu:
        print("\n" + "⚠️ " * 30)
        log("HINWEIS: Es wurde keine NVIDIA GPU erkannt!", "WARN")
        log("Für maximale Geschwindigkeit stelle bitte den Colab-Typ um:", "WARN")
        log("Menü: Runtime -> Change runtime type -> T4 GPU (oder A100)", "WARN")
        print("⚠️ " * 30 + "\n")

    install_cloudflared()
    install_dependencies(has_gpu)

    token = resolve_hf_token()
    model_repo = MODEL_REPO.strip() if 'MODEL_REPO' in globals() else os.getenv("AEGIS_MODEL_REPO", "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF")
    model_file = MODEL_FILE.strip() if 'MODEL_FILE' in globals() else os.getenv("AEGIS_MODEL_FILE", "qwen2.5-coder-7b-instruct-q4_k_m.gguf")

    model_path = download_model(model_repo, model_file, token=token)

    # Determine GPU layers based on hardware and model size
    effective_gpu_layers = GPU_LAYERS
    if not has_gpu:
        effective_gpu_layers = 0
    elif effective_gpu_layers == -1:
        if "27B" in model_repo or "27b" in model_file or "32B" in model_repo or "32b" in model_file:
            effective_gpu_layers = 26  # Smooth fit for 16GB T4 VRAM

    server_proc = start_llama_server(model_path, gpu_layers=effective_gpu_layers)

    # Wait for llama_cpp server to bind port
    time.sleep(5)

    tunnel_proc, raw_tunnel_url = start_cloudflare_tunnel(PORT)
    api_base_url = f"{raw_tunnel_url.rstrip('/')}/v1"

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
