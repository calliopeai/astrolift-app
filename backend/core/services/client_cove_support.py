"""Server-side ClientCove support client.

Credentials and the signed identity assertion stay on the Astrolift server;
the browser only calls Astrolift's own authenticated surface.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import jwt
import requests
from django.conf import settings


def _assertion(*, user, organization) -> str:
    secret = getattr(settings, "CLIENT_COVE_SUPPORT_API_KEY", "")
    if not secret:
        raise RuntimeError("ClientCove support credentials are not configured")
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "iss": "astrolift",
            "aud": "client-cove-support",
            "sub": str(user.pk),
            "email": user.email,
            "org": organization.slug,
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=60)).timestamp()),
            "jti": str(uuid.uuid4()),
        },
        secret,
        algorithm="HS256",
    )


class ClientCoveSupportClient:
    """Minimal entitlement/case client; no telemetry or provisioning methods."""

    def __init__(self, *, user, organization):
        if not getattr(settings, "ASTROLIFT_SUPPORT_ENABLED", False):
            raise RuntimeError("ClientCove support is disabled")
        self.base_url = getattr(settings, "CLIENT_COVE_SUPPORT_URL", "").rstrip("/")
        self.api_key = getattr(settings, "CLIENT_COVE_SUPPORT_API_KEY", "")
        if not self.base_url or not self.api_key:
            raise RuntimeError("ClientCove support is not configured")
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "X-Astrolift-Support-Key": self.api_key,
            "X-Astrolift-Support-Assertion": _assertion(user=user, organization=organization),
            "Accept": "application/json",
        }

    def list_tickets(self) -> dict:
        response = requests.get(f"{self.base_url}/app/api/support/tickets/", headers=self.headers, timeout=10)
        response.raise_for_status()
        return response.json()

    def create_ticket(self, *, product: str, deployment_id: str, title: str, body: str) -> dict:
        response = requests.post(
            f"{self.base_url}/app/api/support/tickets/",
            headers={**self.headers, "Content-Type": "application/json"},
            json={"product": product, "deployment_id": deployment_id, "title": title, "body": body},
            timeout=10,
        )
        response.raise_for_status()
        return response.json()

    def get_ticket(self, ticket_id: str) -> dict:
        response = requests.get(
            f"{self.base_url}/app/api/support/tickets/{ticket_id}/", headers=self.headers, timeout=10
        )
        response.raise_for_status()
        return response.json()

    def comment_ticket(self, ticket_id: str, message: str) -> dict:
        response = requests.post(
            f"{self.base_url}/app/api/support/tickets/{ticket_id}/",
            headers={**self.headers, "Content-Type": "application/json"},
            json={"message": message},
            timeout=10,
        )
        response.raise_for_status()
        return response.json()
