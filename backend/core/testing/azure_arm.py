"""A controlled Azure Resource Manager for workload identity tests (#2279).

The production factory builds real MSI and Authorization SDK clients; with
:func:`install` they send their requests here instead of to Azure. A test then
exercises the platform's request shapes, paging and error mapping, with no
credentials and no network. Only the transport and the token are stand-ins.
"""

from __future__ import annotations

import copy
import json
import re
import time
import uuid
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

TOKEN = "controlled-arm-token"
_PRINCIPAL_FILTER = re.compile(r"principalId eq '([0-9a-f-]{36})'\Z")


class ControlledCredential:
    def __init__(self) -> None:
        self.requests: list[tuple[tuple[str, ...], str | None]] = []

    def get_token(self, *scopes: str, **kwargs: Any) -> Any:
        from azure.core.credentials import AccessToken

        self.requests.append((scopes, kwargs.get("tenant_id")))
        return AccessToken(TOKEN, int(time.time()) + 3600)


def _response(request: Any, status: int, payload: Any) -> Any:
    from azure.core.pipeline.transport import HttpResponse

    content = json.dumps(payload).encode()

    class Response(HttpResponse):
        def body(self) -> bytes:
            return content

    response = Response(request, None)
    response.status_code = status
    response.headers = {"Content-Type": "application/json"}
    response.content_type = "application/json"
    response.reason = "controlled"
    return response


def _absent(request: Any) -> Any:
    return _response(request, 404, {"error": {"code": "ResourceNotFound", "message": "absent"}})


class ControlledARM:
    """One subscription, one AKS cluster and the identities, federations and
    role assignments the platform writes into them."""

    def __init__(
        self,
        *,
        subscription_id: str,
        tenant_id: str,
        resource_group: str,
        cluster_name: str,
        issuer: str,
    ) -> None:
        self.subscription_id = subscription_id
        self.tenant_id = tenant_id
        self.resource_group = resource_group
        self.subscription = {
            "id": f"/subscriptions/{subscription_id}",
            "subscriptionId": subscription_id,
            "tenantId": tenant_id,
            "state": "Enabled",
        }
        self.cluster_path = (
            f"/subscriptions/{subscription_id}/resourceGroups/{resource_group}"
            f"/providers/Microsoft.ContainerService/managedClusters/{cluster_name}"
        )
        self.cluster: dict[str, Any] = {
            "id": self.cluster_path,
            "name": cluster_name,
            "properties": {
                "oidcIssuerProfile": {"enabled": True, "issuerURL": issuer},
                "securityProfile": {"workloadIdentity": {"enabled": True}},
            },
        }
        self.identities: dict[str, dict[str, Any]] = {}
        self.fics: dict[str, dict[str, dict[str, Any]]] = {}
        self.roles: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str, Any]] = []
        self.failures: dict[tuple[str, str], int] = {}
        """``(method, path fragment)`` answered with an error status instead."""
        self.hook: Callable[[str, str], None] = lambda method, path: None

    # ---- what tests seed and read --------------------------------------

    def identity_path(self, name: str) -> str:
        return (
            f"/subscriptions/{self.subscription_id}/resourceGroups/{self.resource_group}"
            f"/providers/Microsoft.ManagedIdentity/userAssignedIdentities/{name}"
        )

    def seed_identity(self, name: str, *, tags: dict[str, str]) -> dict[str, Any]:
        self.identities[name] = {
            "id": self.identity_path(name),
            "name": name,
            "type": "Microsoft.ManagedIdentity/userAssignedIdentities",
            "location": "eastus",
            "tags": dict(tags),
            "properties": {
                "tenantId": self.tenant_id,
                "principalId": str(uuid.uuid4()),
                "clientId": str(uuid.uuid4()),
            },
        }
        self.fics.setdefault(name, {})
        return self.identities[name]

    def seed_federation(self, identity: str, name: str, *, issuer: str, subject: str, audience: str) -> None:
        self.fics.setdefault(identity, {})[name] = {
            "id": f"{self.identity_path(identity)}/federatedIdentityCredentials/{name}",
            "name": name,
            "properties": {"issuer": issuer, "subject": subject, "audiences": [audience]},
        }

    def seed_assignment(
        self, *, scope: str, principal_id: str, role_definition_guid: str, description: str
    ) -> str:
        name = str(uuid.uuid4())
        self.roles[name] = {
            "id": f"{scope}/providers/Microsoft.Authorization/roleAssignments/{name}",
            "name": name,
            "properties": {
                "scope": scope,
                "principalId": principal_id,
                "principalType": "ServicePrincipal",
                "roleDefinitionId": (
                    f"/subscriptions/{self.subscription_id}/providers/Microsoft.Authorization"
                    f"/roleDefinitions/{role_definition_guid}"
                ),
                "description": description,
            },
        }
        return name

    @property
    def writes(self) -> list[tuple[str, str, Any]]:
        return [call for call in self.calls if call[0] != "GET"]

    # ---- the HttpTransport the SDK clients send through -----------------

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def __enter__(self) -> ControlledARM:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def send(self, request: Any, **kwargs: Any) -> Any:
        url = urlsplit(request.url)
        assert url.netloc == "management.azure.com", request.url
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        path = unquote(url.path)
        payload = json.loads(request.body) if request.body else None
        self.calls.append((request.method, path, payload))
        self.hook(request.method, path)
        for (method, fragment), status in self.failures.items():
            if request.method == method and fragment in path:
                return _response(request, status, {"error": {"code": "Controlled", "message": "refused"}})
        if path == f"/subscriptions/{self.subscription_id}":
            return _response(request, 200, copy.deepcopy(self.subscription))
        if path == self.cluster_path:
            return _response(request, 200, copy.deepcopy(self.cluster))
        if (
            path.casefold()
            == f"/subscriptions/{self.subscription_id}/providers/microsoft.authorization/roleassignments"
        ):
            principal = _PRINCIPAL_FILTER.match(parse_qs(url.query).get("$filter", [""])[0])
            assert principal, "role assignments are only ever listed for one principal"
            rows = [
                row for row in self.roles.values() if row["properties"]["principalId"] == principal.group(1)
            ]
            return _response(request, 200, {"value": copy.deepcopy(rows)})
        if "/providers/Microsoft.Authorization/roleAssignments/" in path:
            return self._assignment(request, path, payload)
        identity_root = self.identity_path("")
        assert path.startswith(identity_root), f"unexpected ARM path {path}"
        name, _, rest = path[len(identity_root) :].partition("/")
        if not rest:
            return self._identity(request, name, payload)
        assert rest.startswith("federatedIdentityCredentials"), path
        return self._federation(
            request, name, rest.removeprefix("federatedIdentityCredentials").strip("/"), payload
        )

    def _identity(self, request: Any, name: str, payload: Any) -> Any:
        if request.method == "GET":
            row = self.identities.get(name)
            return _response(request, 200, copy.deepcopy(row)) if row else _absent(request)
        if request.method == "PUT":
            assert name not in self.identities, "an existing identity must never be rewritten"
            row = self.seed_identity(name, tags=payload["tags"])
            row["location"] = payload["location"]
            return _response(request, 201, copy.deepcopy(row))
        assert request.method == "DELETE"
        self.identities.pop(name, None)
        self.fics.pop(name, None)
        return _response(request, 200, {})

    def _federation(self, request: Any, identity: str, name: str, payload: Any) -> Any:
        rows = self.fics.setdefault(identity, {})
        if not name:
            return _response(request, 200, {"value": copy.deepcopy(list(rows.values()))})
        if request.method == "GET":
            return _response(request, 200, copy.deepcopy(rows[name])) if name in rows else _absent(request)
        if request.method == "PUT":
            rows[name] = {
                "id": f"{self.identity_path(identity)}/federatedIdentityCredentials/{name}",
                "name": name,
                "properties": payload["properties"],
            }
            return _response(request, 200, copy.deepcopy(rows[name]))
        assert request.method == "DELETE"
        rows.pop(name, None)
        return _response(request, 200, {})

    def _assignment(self, request: Any, path: str, payload: Any) -> Any:
        scope, _, name = path.partition("/providers/Microsoft.Authorization/roleAssignments/")
        if request.method == "GET":
            row = self.roles.get(name)
            return _response(request, 200, copy.deepcopy(row)) if row else _absent(request)
        if request.method == "PUT":
            self.roles[name] = {
                "id": path,
                "name": name,
                "properties": {**payload["properties"], "scope": scope},
            }
            return _response(request, 201, copy.deepcopy(self.roles[name]))
        assert request.method == "DELETE"
        self.roles.pop(name, None)
        return _response(request, 200, {})


def install(monkeypatch: Any, arm: ControlledARM) -> ControlledCredential:
    """Route the production factory's SDK clients and token through ``arm``."""
    credential = ControlledCredential()
    monkeypatch.setattr("azure.core.pipeline.transport.RequestsTransport", lambda: arm)
    monkeypatch.setattr("azure.identity.DefaultAzureCredential", lambda **_options: credential)
    return credential
