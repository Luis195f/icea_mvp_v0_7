"""WebSocket routing (optional).

Only used when ICEA_ENABLE_CHANNELS=true and Channels is installed.
"""

from __future__ import annotations

from django.urls import re_path

from icea_pipeline.consumers import PingConsumer

websocket_urlpatterns = [
    re_path(r"^ws/ping/$", PingConsumer.as_asgi()),
]
