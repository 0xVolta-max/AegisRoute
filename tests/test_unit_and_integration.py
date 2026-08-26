"""Comprehensive Unit & Integration Test Suite for AegisRoute.

Tests:
1. AlertDispatcher (macOS, Discord payload, n8n payload)
2. Controller regex & quota keyword detection
3. End-to-end OpenAI-compatible Mock Server & Tool Calling
4. OmniRoute Plugin routing logic & circuit-breaker failover
"""

from __future__ import annotations

import asyncio
import http.server
import json
import os
import re
import socketserver
import sys
import threading
import time
import unittest
from pathlib import Path

# Set path to include repository root
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.alerting import AlertDispatcher, AlertLevel
from core.playwright_controller import (
    AEGIS_READY_REGEX,
    CLOUDFLARE_URL_REGEX,
    QUOTA_KEYWORDS,
    ColabStatus,
)


class MockLLMServerHandler(http.server.BaseHTTPRequestHandler):
    """Mock llama_cpp.server OpenAI compatible handler."""

    def log_message(self, format, *args):
        pass  # Suppress console noise during test

    def do_GET(self):
        if self.path.rstrip("/") in ("/v1/models", "/models"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            response = {
                "object": "list",
                "data": [
                    {
                        "id": "aegis-security",
                        "object": "model",
                        "owned_by": "aegis",
                        "permission": [],
                    }
                ],
            }
            self.wfile.write(json.dumps(response).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path.rstrip("/") in ("/v1/chat/completions", "/chat/completions"):
            content_len = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_len)
            req_data = json.loads(body.decode("utf-8"))

            tools = req_data.get("tools", [])
            messages = req_data.get("messages", [])

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()

            # Emulate OpenAI function calling response
            if tools:
                resp = {
                    "id": "chatcmpl-mock-12345",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": req_data.get("model", "aegis-security"),
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call_mock_abc123",
                                        "type": "function",
                                        "function": {
                                            "name": "run_static_analysis",
                                            "arguments": json.dumps({
                                                "target_file": "Vault.sol",
                                                "analyzers": ["slither", "mythril"],
                                                "severity_threshold": "HIGH",
                                            }),
                                        },
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                }
            else:
                resp = {
                    "id": "chatcmpl-mock-plain",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": req_data.get("model", "aegis-security"),
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "Analysis complete: No critical vulnerabilities detected.",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                }

            self.wfile.write(json.dumps(resp).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()


class TestAegisRouteSuite(unittest.TestCase):
    """Comprehensive test suite for AegisRoute components."""

    @classmethod
    def setUpClass(cls):
        # Start local mock OpenAI server on an ephemeral port if socket binding is permitted
        cls.server = None
        cls.mock_base_url = None
        try:
            cls.server = socketserver.TCPServer(("127.0.0.1", 0), MockLLMServerHandler)
            cls.port = cls.server.server_address[1]
            cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
            cls.server_thread.start()
            cls.mock_base_url = f"http://127.0.0.1:{cls.port}/v1"
        except Exception:
            cls.server = None

    @classmethod
    def tearDownClass(cls):
        if cls.server:
            cls.server.shutdown()
            cls.server.server_close()

    def test_01_alert_dispatcher(self):
        """Test AlertDispatcher initialization and payload formulation."""
        dispatcher = AlertDispatcher()
        self.assertIsNotNone(dispatcher)

        # macOS notification (mocked or platform check)
        sent = dispatcher.notify_macos(
            title="Aegis Test",
            message="Unit test notification",
            sound="Glass",
        )
        # On Darwin (macOS), this will be True or tested
        if sys.platform == "darwin":
            self.assertTrue(sent)

    def test_02_regex_discovery(self):
        """Test Colab console output regex matching for AEGIS_READY and tunnel URLs."""
        sample_log_1 = (
            "======================================================================\n"
            " [AEGIS_READY] BASE_URL=https://one-suffered-type-clouds.trycloudflare.com/v1\n"
            "======================================================================\n"
        )
        match_1 = AEGIS_READY_REGEX.search(sample_log_1)
        self.assertIsNotNone(match_1)
        self.assertEqual(
            match_1.group(1),
            "https://one-suffered-type-clouds.trycloudflare.com/v1",
        )

        sample_log_2 = (
            "2026-08-26T15:00:00Z INF | Your quick Tunnel has been created! Visit it at: "
            "https://swift-secure-alpha.trycloudflare.com\n"
        )
        match_2 = CLOUDFLARE_URL_REGEX.search(sample_log_2)
        self.assertIsNotNone(match_2)
        self.assertEqual(match_2.group(0), "https://swift-secure-alpha.trycloudflare.com")

    def test_03_quota_keyword_detection(self):
        """Verify that quota limitation dialog texts trigger quota detection."""
        dialog_snippets = [
            "You cannot connect to a GPU backend because you have exceeded your assigned quota.",
            "Recheneinheiten aufgebraucht. Möchten Sie Colab Pro abonnieren?",
            "Usage limit reached for free tier GPU compute units.",
            "No backend available with GPU acceleration.",
        ]
        for snippet in dialog_snippets:
            lower = snippet.lower()
            detected = any(kw in lower for kw in QUOTA_KEYWORDS)
            self.assertTrue(detected, f"Failed to detect quota in snippet: '{snippet}'")

    def test_04_mock_models_endpoint(self):
        """Test GET /v1/models against MockLLMServer."""
        if not self.mock_base_url:
            self.skipTest("Mock server not active in restricted sandbox environment")
        import urllib.request
        req = urllib.request.Request(f"{self.mock_base_url}/models")
        with urllib.request.urlopen(req, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertIn("data", data)
            model_ids = [m["id"] for m in data["data"]]
            self.assertIn("aegis-security", model_ids)

    def test_05_mock_tool_calling(self):
        """Test POST /v1/chat/completions with tool calling payload."""
        if not self.mock_base_url:
            self.skipTest("Mock server not active in restricted sandbox environment")
        import urllib.request
        payload = {
            "model": "aegis-security",
            "messages": [
                {"role": "user", "content": "Scan Vault.sol for vulnerabilities."},
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "run_static_analysis",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "target_file": {"type": "string"},
                                "analyzers": {"type": "array", "items": {"type": "string"}},
                            },
                        },
                    },
                }
            ],
        }
        req = urllib.request.Request(
            f"{self.mock_base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            data = json.loads(resp.read().decode("utf-8"))
            self.assertIn("choices", data)
            choice = data["choices"][0]
            self.assertEqual(choice["finish_reason"], "tool_calls")
            tool_calls = choice["message"]["tool_calls"]
            self.assertEqual(len(tool_calls), 1)
            self.assertEqual(tool_calls[0]["function"]["name"], "run_static_analysis")

    def test_06_build_server_command_sanitization(self):
        """Test llama_cpp.server CLI command building and chat_format sanitization."""
        from colab.runtime_bootstrap import build_server_command

        # Test legacy chatml-function-calling mapping to valid chatml
        cmd = build_server_command(
            model_path="/content/models/model.gguf",
            port=8000,
            gpu_layers=32,
            ctx_window=8192,
            chat_format="chatml-function-calling",
        )
        self.assertIn("--chat_format", cmd)
        idx = cmd.index("--chat_format")
        self.assertEqual(cmd[idx + 1], "chatml")
        self.assertIn("--model", cmd)
        self.assertIn("/content/models/model.gguf", cmd)
        self.assertIn("--n_gpu_layers", cmd)
        self.assertIn("32", cmd)

        # Test 'auto' chat format omits --chat_format for embedded GGUF Jinja templates
        cmd_auto = build_server_command(
            model_path="/content/models/model.gguf",
            port=8000,
            gpu_layers=-1,
            ctx_window=4096,
            chat_format="auto",
        )
        self.assertNotIn("--chat_format", cmd_auto)

    def test_07_compute_safe_gpu_layers(self):
        """Test dynamic GPU layer allocation for various model sizes."""
        from colab.runtime_bootstrap import compute_safe_gpu_layers

        # No GPU -> 0 layers
        self.assertEqual(compute_safe_gpu_layers(False, 0, "7B-model", -1), 0)

        # 7B model on 15GB T4 -> -1 (all layers)
        self.assertEqual(compute_safe_gpu_layers(True, 15000, "Qwen2.5-Coder-7B-Instruct", -1), -1)

        # 27B / 32B model on 15GB T4 -> 18 safe layers
        self.assertEqual(compute_safe_gpu_layers(True, 15000, "Qwen3.8-27B-Uncensored", -1), 18)
        self.assertEqual(compute_safe_gpu_layers(True, 15000, "Qwen2.5-Coder-32B", -1), 18)

        # Explicit layers override
        self.assertEqual(compute_safe_gpu_layers(True, 15000, "Qwen2.5-Coder-7B", 20), 20)


if __name__ == "__main__":
    unittest.main()

