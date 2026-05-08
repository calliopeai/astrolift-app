"""
ASGI config — multi-protocol routing.

HTTP requests fall through to Django (the existing WSGI surface,
served as ASGI by ``get_asgi_application``). WebSocket requests for
``/app/gql/config/ws/`` are handled by Strawberry's GraphQL-WS
ASGI app, which delivers GraphQL subscriptions.

Why split: Django's URL routing is HTTP-only; WebSocket scope has
to be dispatched at the ASGI layer before Django sees the request.
The ``_select`` callable here is the simplest dispatch — switch on
``scope['type']`` and ``scope['path']``.

Setting ``ASGI_APPLICATION = 'config.asgi.application'`` in
settings.py makes ``runserver`` (Django 6+) auto-switch to ASGI.
For production, use ``daphne`` or ``uvicorn`` against this same
``application`` callable.
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

# Initialize Django before importing anything that touches models.
django_application = get_asgi_application()


def _build_websocket_app():
    """Build the Strawberry WebSocket app lazily.

    Imports happen here (not at module top) so the app loader doesn't
    pull strawberry.asgi (which depends on starlette) into every Django
    management command.
    """
    from strawberry.asgi import GraphQL
    from strawberry.subscriptions import GRAPHQL_TRANSPORT_WS_PROTOCOL

    from config.schema import schema

    return GraphQL(schema, subscription_protocols=[GRAPHQL_TRANSPORT_WS_PROTOCOL])


_websocket_app = None


async def application(scope, receive, send):
    global _websocket_app
    if scope["type"] == "websocket":
        path = scope.get("path", "")
        if path.startswith("/app/gql/config/ws"):
            if _websocket_app is None:
                _websocket_app = _build_websocket_app()
            return await _websocket_app(scope, receive, send)
        # Unknown WS path — close politely.
        await send({"type": "websocket.close", "code": 4404})
        return

    # HTTP / lifespan → Django.
    return await django_application(scope, receive, send)
