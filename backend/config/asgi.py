"""ASGI config for ICEA MVP.

Enterprise mode (optional): if ICEA_ENABLE_CHANNELS=true and Channels is installed,
this will expose WebSockets routes without affecting the default MVP.
"""

from __future__ import annotations

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

django_asgi_app = get_asgi_application()

# Graceful degradation: enable websockets only when explicitly requested and dependency is present.
ENABLE_CHANNELS = os.environ.get("ICEA_ENABLE_CHANNELS", "false").lower() in {"1", "true", "yes"}

if ENABLE_CHANNELS:
    try:
        from channels.auth import AuthMiddlewareStack  # type: ignore
        from channels.routing import ProtocolTypeRouter, URLRouter  # type: ignore

        from config.routing import websocket_urlpatterns

        application = ProtocolTypeRouter(
            {
                "http": django_asgi_app,
                "websocket": AuthMiddlewareStack(URLRouter(websocket_urlpatterns)),
            }
        )
    except Exception:
        # Fallback to plain Django ASGI.
        application = django_asgi_app
else:
    application = django_asgi_app
