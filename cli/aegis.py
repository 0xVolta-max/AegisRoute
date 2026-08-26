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
    tunnel_url = args.url or os.getenv("AEGIS_TUNNEL_URL", "http://localhost:8000/v1").rstrip("/")

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

    # test
    p_test = subparsers.add_parser("test", help="Run OpenAI tool-calling & verification tests")
    p_test.set_defaults(func=cmd_test)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
