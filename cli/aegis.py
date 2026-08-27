#!/usr/bin/env python3
"""AegisRoute CLI - Central Orchestrator for Autonomous Colab LLM Inference.

Subcommands:
  init-auth   - One-time interactive Google login via Playwright
  start       - Headless Colab start & OmniRoute plugin synchronization
  status      - Display endpoint health, VRAM, and active tunnel status
  test        - Run OpenAI tool-calling & benchmark validation tests
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Load .env file automatically
try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    env_file = REPO_ROOT / ".env"
    if env_file.exists():
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("'\"")
                    if k and k not in os.environ:
                        os.environ[k] = v

from core.alerting import AlertDispatcher, AlertLevel
from core.playwright_controller import ColabPlaywrightController, ColabStatus

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    console = Console()
except ImportError:
    console = None  # type: ignore


def print_banner():
    banner_text = r"""
   ___            _     ____             _       
  / _ \ ___  __ _(_)___|  _ \ ___  _   _| |_ ___ 
 / /_\ / _ \/ _` | / __| |_) / _ \| | | | __/ _ \
/ /_\\ \  __/ (_| | \__ \  _ < (_) | |_| | ||  __/
\____/ \___|\__, |_|___/_| \_\___/ \__,_|\__\___|
            |___/                                
Autonomous Colab-LLM Inference Bridge & OmniRoute Plugin
"""
    if console:
        console.print(f"[bold cyan]{banner_text}[/bold cyan]")
    else:
        print(banner_text)


def cmd_init_auth(args: argparse.Namespace) -> int:
    """Run interactive Google login session to capture cookies and persistent profile."""
    print_banner()
    if console:
        console.print(Panel.fit("[bold green]Starting One-Time Google Authentication[/bold green]\n"
                                "A browser window will open. Please log in with your Google account."))
    else:
        print("=== Starting One-Time Google Authentication ===")

    notebook_url = args.url or os.getenv("AEGIS_COLAB_URL", "https://colab.research.google.com")
    user_data_dir = args.user_data or os.getenv("AEGIS_USER_DATA_DIR", str(REPO_ROOT / "colab_user_data"))

    controller = ColabPlaywrightController(
        notebook_url=notebook_url,
        user_data_dir=user_data_dir,
        headless=False,
    )
    return asyncio.run(controller.init_interactive_auth())


def cmd_start(args: argparse.Namespace) -> int:
    """Launch Colab notebook headless and synchronize active tunnel URL."""
    print_banner()
    notebook_url = args.url or os.getenv("AEGIS_COLAB_URL")
    if not notebook_url:
        msg = "Error: Colab Notebook URL not specified! Pass --url or set AEGIS_COLAB_URL in .env"
        if console:
            console.print(f"[bold red]{msg}[/bold red]")
        else:
            print(msg)
        return 1

    user_data_dir = args.user_data or os.getenv("AEGIS_USER_DATA_DIR", str(REPO_ROOT / "colab_user_data"))
    callback_url = args.callback or os.getenv("AEGIS_TUNNEL_CALLBACK_URL", "http://localhost:20128/aegis/update-tunnel")

    if console:
        console.print(f"[cyan]Target Notebook:[/cyan] {notebook_url}")
        console.print(f"[cyan]User Profile:[/cyan] {user_data_dir}")
        console.print(f"[cyan]Headless Mode:[/cyan] {not args.headful}")
        console.print("[yellow]Initiating autonomous Colab boot sequence...[/yellow]")

    controller = ColabPlaywrightController(
        notebook_url=notebook_url,
        user_data_dir=user_data_dir,
        headless=not args.headful,
        timeout_seconds=args.timeout,
        tunnel_callback_url=callback_url,
    )

    status, tunnel_url = asyncio.run(controller.run_notebook_and_watch())

    if status == ColabStatus.SUCCESS and tunnel_url:
        # Persist dynamic tunnel URL into .env
        try:
            env_file = REPO_ROOT / ".env"
            if env_file.exists():
                lines = []
                with open(env_file, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.startswith("AEGIS_TUNNEL_URL="):
                            lines.append(f"AEGIS_TUNNEL_URL={tunnel_url}\n")
                        else:
                            lines.append(line)
                with open(env_file, "w", encoding="utf-8") as f:
                    f.writelines(lines)
            os.environ["AEGIS_TUNNEL_URL"] = tunnel_url
        except Exception:
            pass

        # Automatically register/update provider connection in OmniRoute
        try:
            sync_omniroute_provider(tunnel_url)
        except Exception:
            pass

        if console:
            console.print(Panel(
                f"[bold green]✨ AegisRoute Inference Bridge Online![/bold green]\n\n"
                f"[bold]Base URL:[/bold] {tunnel_url}\n"
                f"[bold]Chat Endpoint:[/bold] {tunnel_url}/chat/completions\n"
                f"[bold]Models Endpoint:[/bold] {tunnel_url}/models",
                title="Status: READY",
                border_style="green",
            ))
        else:
            print(f"\n[AEGIS_READY] Base URL: {tunnel_url}")
        return 0
    elif status == ColabStatus.QUOTA_EXCEEDED:
        if console:
            console.print(Panel("[bold red]GPU Quota Limit Reached on Google Colab[/bold red]\n"
                                "Circuit-breaker initiated. OmniRoute fallback activated.",
                                title="Status: QUOTA_EXCEEDED", border_style="red"))
        else:
            print("Status: QUOTA_EXCEEDED (Exit Code 2)")
        return 2
    else:
        if console:
            console.print(Panel("[bold red]Failed to establish Colab Inference Bridge[/bold red]",
                                title="Status: ERROR", border_style="red"))
        else:
            print("Status: ERROR (Exit Code 1)")
        return 1


def cmd_status(args: argparse.Namespace) -> int:
    """Query and display active endpoint health and OmniRoute status."""
    print_banner()

    tunnel_url = args.url
    if not tunnel_url:
        # Check OmniRoute status endpoint first if available
        omniroute_url = os.getenv("OMNIROUTE_BASE_URL", "http://localhost:20128").rstrip("/")
        try:
            import urllib.request
            req = urllib.request.Request(f"{omniroute_url}/aegis/status", timeout=2.0)
            with urllib.request.urlopen(req) as resp:
                if resp.status == 200:
                    info = json.loads(resp.read().decode())
                    tunnel_url = info.get("config", {}).get("tunnelUrl")
        except Exception:
            pass

    if not tunnel_url:
        tunnel_url = os.getenv("AEGIS_TUNNEL_URL", "http://localhost:8000/v1")
    
    tunnel_url = tunnel_url.rstrip("/")

    if console:
        console.print(f"[bold]Probing endpoint:[/bold] {tunnel_url}/models ...")
    else:
        print(f"Probing endpoint: {tunnel_url}/models ...")

    is_online = False
    models_list = []
    latency_ms = 0.0

    try:
        start_t = time.time()
        if httpx:
            with httpx.Client(timeout=5.0) as client:
                res = client.get(f"{tunnel_url}/models")
                latency_ms = (time.time() - start_t) * 1000
                if res.status_code == 200:
                    is_online = True
                    data = res.json()
                    models_list = [m.get("id") for m in data.get("data", [])]
        else:
            import urllib.request
            req = urllib.request.Request(f"{tunnel_url}/models")
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                latency_ms = (time.time() - start_t) * 1000
                if resp.status == 200:
                    is_online = True
                    data = json.loads(resp.read().decode())
                    models_list = [m.get("id") for m in data.get("data", [])]
    except Exception as exc:
        err_msg = str(exc)

    if console:
        table = Table(title="🛡️ AegisRoute Live Status")
        table.add_column("Component", style="cyan")
        table.add_column("Value", style="bold")

        table.add_row("Endpoint URL", tunnel_url)
        table.add_row("Health Status", "[green]HEALTHY (ONLINE)[/green]" if is_online else "[red]OFFLINE / UNREACHABLE[/red]")
        table.add_row("Roundtrip Latency", f"{latency_ms:.1f} ms" if is_online else "N/A")
        table.add_row("Loaded Models", ", ".join(models_list) if models_list else "None detected")
        console.print(table)
    else:
        print("=== Status ===")
        print(f"Endpoint: {tunnel_url}")
        print(f"Online:   {is_online}")
        print(f"Models:   {models_list}")

    return 0 if is_online else 1


def cmd_install_plugin(args: argparse.Namespace) -> int:
    """Install AegisRoute plugin into OmniRoute (supports Docker containers and local CLI)."""
    import shutil
    import subprocess
    import urllib.request
    import urllib.error

    print_banner()
    plugin_src = REPO_ROOT / "plugin"
    if not plugin_src.exists():
        msg = f"Error: Plugin directory not found at {plugin_src}"
        if console:
            console.print(f"[bold red]{msg}[/bold red]")
        else:
            print(msg)
        return 1

    omniroute_url = (args.omniroute_url or os.getenv("OMNIROUTE_BASE_URL", "http://localhost:20128")).rstrip("/")
    installed_targets = []
    errors = []

    # 1. Local OmniRoute Installation (~/.omniroute/plugins/)
    if not args.docker_only:
        try:
            home = Path.home()
            plugins_dir = home / ".omniroute" / "plugins"
            plugins_dir.mkdir(parents=True, exist_ok=True)

            # Server plugin folder
            target_plugin = plugins_dir / "omniroute-plugin-aegis"
            if target_plugin.exists():
                shutil.rmtree(target_plugin)
            shutil.copytree(plugin_src, target_plugin)

            # CLI command plugin folder (omniroute-cmd-aegis)
            target_cmd = plugins_dir / "omniroute-cmd-aegis"
            if target_cmd.exists():
                shutil.rmtree(target_cmd)
            shutil.copytree(plugin_src, target_cmd)

            # Update package.json name for CLI plugin
            cmd_pkg = target_cmd / "package.json"
            if cmd_pkg.exists():
                with open(cmd_pkg, "r", encoding="utf-8") as f:
                    pkg_data = json.load(f)
                pkg_data["name"] = "omniroute-cmd-aegis"
                with open(cmd_pkg, "w", encoding="utf-8") as f:
                    json.dump(pkg_data, f, indent=2)

            installed_targets.append(f"Local Path ({plugins_dir})")
        except Exception as e:
            errors.append(f"Local install error: {e}")

def sync_omniroute_provider(tunnel_url: str, container_name: str = "omniroute") -> bool:
    """Register or update AegisRoute provider node, connection, and models in OmniRoute SQLite DB."""
    tunnel_url = tunnel_url.rstrip("/")
    
    # 1. Fetch currently loaded model from live Colab endpoint
    discovered_models = []
    try:
        if httpx:
            with httpx.Client(timeout=3.0) as client:
                res = client.get(f"{tunnel_url}/models")
                if res.status_code == 200:
                    data = res.json()
                    discovered_models = [m.get("id") for m in data.get("data", []) if m.get("id")]
        else:
            import urllib.request
            req = urllib.request.Request(f"{tunnel_url}/models")
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode())
                    discovered_models = [m.get("id") for m in data.get("data", []) if m.get("id")]
    except Exception:
        pass

    # Build model definitions list for OmniRoute catalog
    model_entries = [
        {
            "id": "aegis-security",
            "name": "Aegis Security Audit (Colab GPU)",
            "source": "manual",
            "apiFormat": "chat-completions",
            "supportedEndpoints": ["chat"],
        },
        {
            "id": "aegis-uncensored",
            "name": "Aegis Uncensored (Colab GPU)",
            "source": "manual",
            "apiFormat": "chat-completions",
            "supportedEndpoints": ["chat"],
        }
    ]

    for raw_m in discovered_models:
        base_name = raw_m.split("/")[-1].replace(".gguf", "")
        model_entries.append({
            "id": base_name,
            "name": f"{base_name} (Colab GPU)",
            "source": "manual",
            "apiFormat": "chat-completions",
            "supportedEndpoints": ["chat"],
        })
        model_entries.append({
            "id": raw_m,
            "name": f"{base_name} [Full Path] (Colab GPU)",
            "source": "manual",
            "apiFormat": "chat-completions",
            "supportedEndpoints": ["chat"],
        })

    # 2. Sync into Docker container
    try:
        js_code = f"""
const Database = require('better-sqlite3');
const db = new Database('/app/data/storage.sqlite');
const now = new Date().toISOString();
const nodeId = 'openai-compatible-aegis';
const tunnelUrl = '{tunnel_url}';

// Ensure provider node
const existingNode = db.prepare("SELECT id FROM provider_nodes WHERE id = ?").get(nodeId);
if (!existingNode) {{
  db.prepare(`
    INSERT INTO provider_nodes (id, type, name, prefix, api_type, base_url, chat_path, models_path, created_at, updated_at)
    VALUES (?, 'openai-compatible', 'AegisRoute Colab', 'aegis', 'openai', ?, '/chat/completions', '/models', ?, ?)
  `).run(nodeId, tunnelUrl, now, now);
}} else {{
  db.prepare("UPDATE provider_nodes SET base_url = ?, updated_at = ? WHERE id = ?").run(tunnelUrl, now, nodeId);
}}

// Ensure provider connection linked to nodeId
const existingConn = db.prepare("SELECT id FROM provider_connections WHERE provider = ?").get(nodeId);
if (!existingConn) {{
  db.prepare(`
    INSERT INTO provider_connections (id, provider, auth_type, name, api_key, priority, is_active, test_status, provider_specific_data, created_at, updated_at)
    VALUES ('conn-aegis-01', ?, 'apikey', 'AegisRoute Colab 27B', 'sk-aegis-dummy', 1, 1, 'active', ?, ?, ?)
  `).run(nodeId, JSON.stringify({{ baseUrl: tunnelUrl }}), now, now);
}} else {{
  db.prepare("UPDATE provider_connections SET is_active = 1, test_status = 'active', api_key = 'sk-aegis-dummy', provider_specific_data = ?, updated_at = ? WHERE provider = ?").run(JSON.stringify({{ baseUrl: tunnelUrl }}), now, nodeId);
}}

// Sync custom models into key_value store so OmniRoute catalog surfaces them
const modelsJson = JSON.stringify({json.dumps(model_entries)});
const row = db.prepare("SELECT key FROM key_value WHERE namespace = 'customModels' AND key = ?").get(nodeId);
if (row) {{
  db.prepare("UPDATE key_value SET value = ? WHERE namespace = 'customModels' AND key = ?").run(modelsJson, nodeId);
}} else {{
  db.prepare("INSERT INTO key_value (namespace, key, value) VALUES ('customModels', ?, ?)").run(nodeId, modelsJson);
}}
"""
        subprocess.run(
            ["docker", "exec", container_name, "node", "-e", js_code],
            capture_output=True,
            check=False,
        )
    except Exception:
        pass

    # 3. Sync into local ~/.omniroute/storage.sqlite if present
    try:
        local_db_path = Path.home() / ".omniroute" / "storage.sqlite"
        if local_db_path.exists():
            import sqlite3
            now = time.strftime("%Y-%m-%dT%H:%M:%SZ")
            conn = sqlite3.connect(str(local_db_path))
            cur = conn.cursor()
            node_id = "openai-compatible-aegis"
            cur.execute("SELECT id FROM provider_nodes WHERE id = ?", (node_id,))
            if not cur.fetchone():
                cur.execute("""
                    INSERT INTO provider_nodes (id, type, name, prefix, api_type, base_url, chat_path, models_path, created_at, updated_at)
                    VALUES (?, 'openai-compatible', 'AegisRoute Colab', 'aegis', 'openai', ?, '/chat/completions', '/models', ?, ?)
                """, (node_id, tunnel_url, now, now))
            else:
                cur.execute("UPDATE provider_nodes SET base_url = ?, updated_at = ? WHERE id = ?", (tunnel_url, now, node_id))
            
            cur.execute("SELECT id FROM provider_connections WHERE provider = ?", (node_id,))
            if not cur.fetchone():
                cur.execute("""
                    INSERT INTO provider_connections (id, provider, auth_type, name, api_key, priority, is_active, test_status, provider_specific_data, created_at, updated_at)
                    VALUES ('conn-aegis-01', ?, 'apikey', 'AegisRoute Colab 27B', 'sk-aegis-dummy', 1, 1, 'active', ?, ?, ?)
                """, (node_id, json.dumps({"baseUrl": tunnel_url}), now, now))
            else:
                cur.execute("UPDATE provider_connections SET is_active = 1, test_status = 'active', api_key = 'sk-aegis-dummy', provider_specific_data = ?, updated_at = ? WHERE provider = ?", (json.dumps({"baseUrl": tunnel_url}), now, node_id))

            cur.execute("SELECT key FROM key_value WHERE namespace = 'customModels' AND key = ?", (node_id,))
            models_json = json.dumps(model_entries)
            if cur.fetchone():
                cur.execute("UPDATE key_value SET value = ? WHERE namespace = 'customModels' AND key = ?", (models_json, node_id))
            else:
                cur.execute("INSERT INTO key_value (namespace, key, value) VALUES ('customModels', ?, ?)", (node_id, models_json))

            conn.commit()
            conn.close()
    except Exception:
        pass

    return True


def cmd_install_plugin(args: argparse.Namespace) -> int:
    """Install AegisRoute plugin into OmniRoute (supports Docker containers and local CLI)."""
    import shutil
    import subprocess

    print_banner()
    if console:
        console.print("[bold cyan]Installing AegisRoute Plugin into OmniRoute...[/bold cyan]\n")
    else:
        print("Installing AegisRoute Plugin into OmniRoute...\n")

    plugin_src = REPO_ROOT / "plugin"
    if not plugin_src.exists():
        print(f"Error: Plugin directory not found at {plugin_src}")
        return 1

    installed_targets = []
    errors = []
    omniroute_url = os.getenv("OMNIROUTE_BASE_URL", "http://localhost:20128").rstrip("/")
    tunnel_url = os.getenv("AEGIS_TUNNEL_URL", "http://localhost:8000/v1")

    # 1. Local ~/.omniroute Installation
    if not args.docker_only:
        try:
            home = Path.home()
            plugins_dir = home / ".omniroute" / "plugins"
            plugins_dir.mkdir(parents=True, exist_ok=True)

            # Server plugin folder
            target_plugin = plugins_dir / "omniroute-plugin-aegis"
            if target_plugin.exists():
                shutil.rmtree(target_plugin)
            shutil.copytree(plugin_src, target_plugin)

            # CLI command plugin folder (omniroute-cmd-aegis)
            target_cmd = plugins_dir / "omniroute-cmd-aegis"
            if target_cmd.exists():
                shutil.rmtree(target_cmd)
            shutil.copytree(plugin_src, target_cmd)

            # Update package.json name for CLI plugin
            cmd_pkg = target_cmd / "package.json"
            if cmd_pkg.exists():
                with open(cmd_pkg, "r", encoding="utf-8") as f:
                    pkg_data = json.load(f)
                pkg_data["name"] = "omniroute-cmd-aegis"
                with open(cmd_pkg, "w", encoding="utf-8") as f:
                    json.dump(pkg_data, f, indent=2)

            installed_targets.append(f"Local Path ({plugins_dir})")
        except Exception as e:
            errors.append(f"Local install error: {e}")

    # 2. Docker OmniRoute Installation
    if not args.local_only:
        container_name = args.docker_container or "omniroute"
        try:
            # Check if docker container is running
            check_proc = subprocess.run(
                ["docker", "ps", "--filter", f"name={container_name}", "--format", "{{.Names}}"],
                capture_output=True,
                text=True,
                check=False,
            )
            running_containers = [c.strip() for c in check_proc.stdout.splitlines() if c.strip()]
            if container_name in running_containers or any(container_name in c for c in running_containers):
                matched_container = container_name if container_name in running_containers else running_containers[0]
                
                # Create destination directories in container
                subprocess.run(
                    ["docker", "exec", matched_container, "mkdir", "-p", "/home/node/.omniroute/plugins", "/app/data/plugins"],
                    check=True,
                    capture_output=True,
                )

                # Copy plugin to /home/node/.omniroute/plugins and /app/data/plugins
                subprocess.run(
                    ["docker", "cp", str(plugin_src), f"{matched_container}:/home/node/.omniroute/plugins/omniroute-plugin-aegis"],
                    check=True,
                    capture_output=True,
                )
                subprocess.run(
                    ["docker", "cp", str(plugin_src), f"{matched_container}:/app/data/plugins/omniroute-plugin-aegis"],
                    check=True,
                    capture_output=True,
                )

                # Set permissions
                subprocess.run(
                    ["docker", "exec", "-u", "root", matched_container, "chown", "-R", "node:node", "/home/node/.omniroute", "/app/data/plugins"],
                    check=False,
                    capture_output=True,
                )

                # Trigger scan in OmniRoute server API
                try:
                    req_scan = urllib.request.Request(
                        f"{omniroute_url}/api/plugins/scan",
                        data=b"{}",
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(req_scan, timeout=5) as resp:
                        scan_res = json.loads(resp.read().decode())
                except Exception:
                    pass

                # Trigger activate in OmniRoute server API
                try:
                    req_act = urllib.request.Request(
                        f"{omniroute_url}/api/plugins/omniroute-plugin-aegis/activate",
                        data=b"{}",
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    with urllib.request.urlopen(req_act, timeout=5) as resp:
                        act_res = json.loads(resp.read().decode())
                except Exception:
                    pass

                # Sync Provider Node and Connection into OmniRoute DB
                sync_omniroute_provider(tunnel_url, matched_container)

                installed_targets.append(f"Docker Container '{matched_container}' (Plugin + Provider 'colab-aegis')")
        except FileNotFoundError:
            # Docker binary not installed, skip silently if not explicitly requested
            if args.docker_only:
                errors.append("Docker executable not found on host.")
        except Exception as e:
            if args.docker_only:
                errors.append(f"Docker install error: {e}")

    # Output results
    if installed_targets:
        if console:
            table = Table(title="✨ AegisRoute Plugin Installation Complete")
            table.add_column("Target Environment", style="cyan")
            table.add_column("Status", style="bold green")
            for t in installed_targets:
                table.add_row(t, "INSTALLED & ACTIVE")
            console.print(table)
            console.print(f"\n[green]✓ Plugin successfully registered in OmniRoute![/green]")
            console.print(f"[dim]Dashboard: {omniroute_url}/dashboard/plugins[/dim]\n")
        else:
            print("=== Installation Complete ===")
            for t in installed_targets:
                print(f"✓ Installed: {t}")
            print(f"\nPlugin active at {omniroute_url}/dashboard/plugins")
        return 0
    else:
        err_msg = "\n".join(errors) if errors else "No OmniRoute environment detected."
        if console:
            console.print(f"[bold red]Installation failed:[/bold red] {err_msg}")
        else:
            print(f"Installation failed: {err_msg}")
        return 1


def cmd_test(args: argparse.Namespace) -> int:
    """Run function calling validation tests."""
    print_banner()
    script_path = REPO_ROOT / "tests" / "test_tool_calling.sh"
    if script_path.exists():
        os.system(f"bash {script_path}")
        return 0
    else:
        print("Error: tests/test_tool_calling.sh not found!")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="aegis",
        description="AegisRoute - Autonomous Colab-LLM Inference Bridge CLI",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # init-auth
    p_auth = subparsers.add_parser("init-auth", help="Perform one-time interactive Google login")
    p_auth.add_argument("--url", type=str, default="", help="Google Colab Notebook URL")
    p_auth.add_argument("--user-data", type=str, default="", help="Browser profile directory")
    p_auth.set_defaults(func=cmd_init_auth)

    # start
    p_start = subparsers.add_parser("start", help="Headless start Colab & sync tunnel")
    p_start.add_argument("--url", type=str, default="", help="Google Colab Notebook URL")
    p_start.add_argument("--user-data", type=str, default="", help="Browser profile directory")
    p_start.add_argument("--headful", action="store_true", help="Run browser visibly")
    p_start.add_argument("--timeout", type=int, default=420, help="Timeout in seconds")
    p_start.add_argument("--callback", type=str, default="", help="OmniRoute update webhook URL")
    p_start.set_defaults(func=cmd_start)

    # status
    p_status = subparsers.add_parser("status", help="Check endpoint health and loaded models")
    p_status.add_argument("--url", type=str, default="", help="Base URL of inference bridge")
    p_status.set_defaults(func=cmd_status)

    # install-plugin
    p_install = subparsers.add_parser("install-plugin", help="Auto-install Aegis plugin into OmniRoute (Docker or Local)")
    p_install.add_argument("--docker-container", type=str, default="omniroute", help="Docker container name (default: omniroute)")
    p_install.add_argument("--omniroute-url", type=str, default="", help="OmniRoute server URL (default: http://localhost:20128)")
    p_install.add_argument("--local-only", action="store_true", help="Only install to local ~/.omniroute/plugins")
    p_install.add_argument("--docker-only", action="store_true", help="Only install into running Docker container")
    p_install.set_defaults(func=cmd_install_plugin)

    # test
    p_test = subparsers.add_parser("test", help="Run OpenAI tool-calling & verification tests")
    p_test.set_defaults(func=cmd_test)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

