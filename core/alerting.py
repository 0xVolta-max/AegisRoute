"""Multi-channel alerting dispatcher for AegisRoute.

Supports:
- macOS native notifications via osascript with sound alerts (Basso / Glass)
- Discord Webhooks with rich embed formatting
- n8n Webhook triggers with structured JSON payload
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [AegisRoute.Alerting] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("AegisRoute.Alerting")


class AlertLevel(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"
    RECOVERY = "RECOVERY"


class AlertDispatcher:
    """Dispatches notifications across configured alerting channels."""

    DISCORD_COLOR_CRITICAL = 15158332  # Red (#E74C3C)
    DISCORD_COLOR_WARNING = 15105570   # Orange (#E67E22)
    DISCORD_COLOR_INFO = 3447003       # Blue (#3498DB)
    DISCORD_COLOR_RECOVERY = 3066993   # Green (#2ECC71)

    def __init__(
        self,
        discord_webhook_url: Optional[str] = None,
        n8n_webhook_url: Optional[str] = None,
        enable_macos_notifications: bool = True,
    ) -> None:
        self.discord_webhook_url = discord_webhook_url or os.getenv("AEGIS_DISCORD_WEBHOOK_URL")
        self.n8n_webhook_url = n8n_webhook_url or os.getenv("AEGIS_N8N_WEBHOOK_URL")
        self.enable_macos = enable_macos_notifications

    def notify_macos(
        self,
        title: str,
        message: str,
        subtitle: Optional[str] = None,
        sound: Optional[str] = None,
    ) -> bool:
        """Send a native macOS notification using osascript."""
        if not self.enable_macos or sys.platform != "darwin":
            return False

        try:
            # Escape double quotes for AppleScript string literals
            escaped_msg = message.replace('"', '\\"')
            escaped_title = title.replace('"', '\\"')
            script_parts = [f'display notification "{escaped_msg}" with title "{escaped_title}"']

            if subtitle:
                escaped_sub = subtitle.replace('"', '\\"')
                script_parts.append(f'subtitle "{escaped_sub}"')

            if sound:
                script_parts.append(f'sound name "{sound}"')

            script = " ".join(script_parts)
            subprocess.run(["osascript", "-e", script], check=True, capture_output=True)
            logger.info("macOS alert sent: %s - %s", title, message)
            return True
        except Exception as exc:
            logger.warning("Failed to dispatch macOS notification: %s", exc)
            return False

    def notify_discord(
        self,
        title: str,
        description: str,
        level: AlertLevel = AlertLevel.CRITICAL,
        fields: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Send a rich embed message to a Discord webhook."""
        if not self.discord_webhook_url:
            logger.debug("Discord webhook URL not set; skipping Discord notification.")
            return False

        if level == AlertLevel.RECOVERY:
            color = self.DISCORD_COLOR_RECOVERY
        elif level in (AlertLevel.ERROR, AlertLevel.CRITICAL):
            color = self.DISCORD_COLOR_CRITICAL
        elif level == AlertLevel.WARNING:
            color = self.DISCORD_COLOR_WARNING
        else:
            color = self.DISCORD_COLOR_INFO

        embed_fields = []
        if fields:
            for k, v in fields.items():
                embed_fields.append({"name": str(k), "value": f"`{v}`" if not str(v).startswith("http") else str(v), "inline": True})

        payload = {
            "username": "AegisRoute Sentinel",
            "avatar_url": "https://raw.githubusercontent.com/aegisroute/branding/main/sentinel.png",
            "embeds": [
                {
                    "title": f"🛡️ AegisRoute [{level.value}]: {title}",
                    "description": description,
                    "color": color,
                    "fields": embed_fields,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "footer": {"text": "AegisRoute • Autonomous Colab-LLM Inference Bridge"},
                }
            ],
        }

        return self._http_post(self.discord_webhook_url, payload, "Discord")

    def notify_n8n(
        self,
        event: str,
        provider: str = "colab-aegis",
        duration: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Trigger an n8n webhook workflow with structured JSON payload."""
        if not self.n8n_webhook_url:
            logger.debug("n8n webhook URL not set; skipping n8n notification.")
            return False

        payload = {
            "event": event,
            "provider": provider,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "duration": duration,
            "metadata": metadata or {},
        }

        return self._http_post(self.n8n_webhook_url, payload, "n8n")

    def dispatch_alert(
        self,
        title: str,
        message: str,
        level: AlertLevel = AlertLevel.CRITICAL,
        provider: str = "colab-aegis",
        duration: Optional[float] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, bool]:
        """Broadcast alert to all configured channels."""
        sound = "Basso" if level in (AlertLevel.ERROR, AlertLevel.CRITICAL) else "Glass"
        if level == AlertLevel.INFO:
            sound = "Submarine"

        results = {
            "macos": self.notify_macos(title=f"AegisRoute: {title}", message=message, subtitle=level.value, sound=sound),
            "discord": self.notify_discord(title=title, description=message, level=level, fields=extra),
            "n8n": self.notify_n8n(event=title, provider=provider, duration=duration, metadata=extra),
        }
        return results

    def _http_post(self, url: str, payload: Dict[str, Any], service_name: str) -> bool:
        """Internal synchronous helper for webhook calls."""
        try:
            if httpx:
                with httpx.Client(timeout=10.0) as client:
                    resp = client.post(url, json=payload)
                    resp.raise_for_status()
            else:
                import urllib.request
                data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    url,
                    data=data,
                    headers={"Content-Type": "application/json", "User-Agent": "AegisRoute/1.0"},
                )
                with urllib.request.urlopen(req, timeout=10.0) as resp:
                    if resp.status >= 400:
                        raise RuntimeError(f"HTTP Status {resp.status}")
            logger.info("Alert delivered to %s successfully.", service_name)
            return True
        except Exception as exc:
            logger.error("Failed delivering alert to %s: %s", service_name, exc)
            return False


if __name__ == "__main__":
    dispatcher = AlertDispatcher()
    print("Testing AegisRoute Alert Dispatcher...")
    dispatcher.dispatch_alert(
        title="Quota Limit Reached",
        message="Colab runtime encountered GPU computation limits. Fallback provider activated.",
        level=AlertLevel.CRITICAL,
        provider="colab-aegis",
        extra={"model": "0xalpha/Security-Audit-7B-GGUF", "retry_after_hours": 4},
    )
