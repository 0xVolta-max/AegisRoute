"""AegisRoute Colab Runtime Bootstrap Script.

Paste this entire script into a GPU-enabled Google Colab notebook cell and run it.
It will:
1. Detect GPU architecture & VRAM.
2. Silently install cloudflared & Python dependencies.
3. Install/compile llama-cpp-python with CUDA hardware acceleration.
4. Download the target specialized GGUF security/coding model.
5. Launch llama_cpp.server with ChatML Function-Calling & OpenAI compatibility.
6. Establish a Cloudflare tunnel and output the [AEGIS_READY] BASE_URL marker.
"""

import os
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Optional

# ==========================================
# CONFIGURATION & HYPERPARAMETERS
# ==========================================
DEFAULT_MODEL_REPO = os.getenv("AEGIS_MODEL_REPO", "0xalpha/Security-Audit-7B-GGUF")
DEFAULT_MODEL_FILE = os.getenv("AEGIS_MODEL_FILE", "model-q4_k_m.gguf")
CONTEXT_WINDOW = int(os.getenv("AEGIS_CONTEXT_WINDOW", "8192"))
PORT = int(os.getenv("AEGIS_PORT", "8000"))
CHAT_FORMAT = os.getenv("AEGIS_CHAT_FORMAT", "chatml-function-calling")


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


def get_gpu_info() -> str:
    try:
        res = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader"],
            text=True,
        )
        return res.strip()
    except Exception:
        return "No NVIDIA GPU detected!"


def install_cloudflared() -> None:
    if shutil.which("cloudflared"):
        log("cloudflared already installed.")
        return

    log("Downloading and installing cloudflared...")
    deb_url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb"
    cmd = (
        f"wget -q -O /tmp/cloudflared.deb {deb_url} && "
        "dpkg -i /tmp/cloudflared.deb > /dev/null 2>&1 || "
        "apt-get install -f -y > /dev/null 2>&1"
    )
    run_command(cmd)


def install_dependencies() -> None:
    log("Installing Python dependencies (huggingface_hub, jsonschema, jinja2)...")
    run_command("pip install -q --upgrade pip huggingface_hub jsonschema jinja2 pydantic fastapi uvicorn sse-starlette httpx")

    log("Installing llama-cpp-python with CUDA support...")
    # Check if pre-built CUDA wheel is installable or compile with GGML_CUDA=on
    cuda_env = os.environ.copy()
    cuda_env["CMAKE_ARGS"] = "-DGGML_CUDA=on"
    cuda_env["FORCE_CMAKE"] = "1"
    
    # Try fast installation via prebuilt wheel repo or fallback to source build
    install_cmd = (
        "pip install -q llama-cpp-python[server] "
        "--extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu122 || "
        "pip install -q llama-cpp-python[server] "
        "--extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu121 || "
        "pip install -q llama-cpp-python[server]"
    )
    run_command(install_cmd, env=cuda_env)


def download_model(repo_id: str, filename: str) -> str:
    log(f"Downloading model {filename} from HuggingFace repo: {repo_id}...")
    from huggingface_hub import hf_hub_download

    model_path = hf_hub_download(
        repo_id=repo_id,
        filename=filename,
        local_dir="/content/models",
    )
    log(f"Model downloaded successfully to: {model_path}")
    return model_path


def start_llama_server(model_path: str, gpu_layers: int = -1) -> subprocess.Popen:
    log(f"Starting llama_cpp.server on port {PORT} with chat_format='{CHAT_FORMAT}' and n_ctx={CONTEXT_WINDOW}...")
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
    
    gpu_status = get_gpu_info()
    log(f"GPU Hardware: {gpu_status}")

    install_cloudflared()
    install_dependencies()

    model_repo = os.getenv("AEGIS_MODEL_REPO", DEFAULT_MODEL_REPO)
    model_file = os.getenv("AEGIS_MODEL_FILE", DEFAULT_MODEL_FILE)
    model_path = download_model(model_repo, model_file)

    # Determine GPU layers based on model size
    gpu_layers = -1
    if "27B" in model_repo or "27b" in model_file or "32B" in model_repo or "32b" in model_file:
        gpu_layers = 26  # Fits smoothly into T4/16GB VRAM with offloading

    server_proc = start_llama_server(model_path, gpu_layers=gpu_layers)

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
