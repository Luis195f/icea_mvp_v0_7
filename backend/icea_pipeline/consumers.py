"""Realtime consumers (enterprise optional).

This module is intentionally lightweight: it provides a health/ping WebSocket
endpoint to validate ASGI + Channels deployments.

- ws://<host>/ws/ping/

If Channels is not installed, this module should not be imported.
"""

from __future__ import annotations

import os

from channels.generic.websocket import AsyncJsonWebsocketConsumer  # type: ignore


class PingConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        await self.accept()
        await self.send_json(
            {
                "type": "hello",
                "service": "icea",
                "version": os.environ.get("ICEA_VERSION", "0.7.0"),
            }
        )

    async def receive_json(self, content, **kwargs):
        action = str((content or {}).get("action") or "").lower()
        if action == "ping" or not action:
            await self.send_json({"type": "pong"})
        else:
            await self.send_json({"type": "error", "detail": "unknown action", "action": action})

    async def disconnect(self, close_code):
        return
