"""Independent, retryable CNPG preview credentials and namespace-qualified bindings."""

from __future__ import annotations

import base64
import secrets
from typing import Any
from urllib.parse import quote

from _sdk.managed_service import Binding, SliceResult, SliceSpec, ValueRef
from k8s_native.managed._handle import ParsedHandle, pack, unpack
from k8s_native.managed._service_dns import service_host


def _identity(driver: Any, spec: SliceSpec) -> tuple[ParsedHandle, str, str, str, str, str]:
    parsed = unpack(spec.parent.handle)
    if parsed.kind != "postgres":
        raise ValueError("CNPG slice parent must be a postgres handle")
    host = service_host(parsed, name=f"{parsed.name}-rw")
    identities = (spec.organization_id, spec.app_id, spec.environment_id, spec.parent.managed_service_id)
    if not all(value and "/" not in value and value not in (".", "..") for value in identities):
        raise ValueError("CNPG slice requires immutable organization, app, environment and service identities")
    database, role, secret_name = driver._slice_names(spec)
    path = (
        f"services/{spec.organization_id}/{spec.app_id}/cnpg-slices/"
        f"{spec.parent.managed_service_id}/{spec.environment_id}"
    )
    return parsed, host, database, role, secret_name, path


def slice_binding(driver: Any, spec: SliceSpec) -> Binding:
    _parsed, host, database, role, _secret_name, path = _identity(driver, spec)
    return Binding(
        env_vars={
            "POSTGRES_HOST": ValueRef(literal=host),
            "DATABASE_HOST": ValueRef(literal=host),
            "POSTGRES_PORT": ValueRef(literal="5432"),
            "DATABASE_PORT": ValueRef(literal="5432"),
            "POSTGRES_DB": ValueRef(literal=database),
            "DATABASE_NAME": ValueRef(literal=database),
            "POSTGRES_USER": ValueRef(literal=role),
            "DATABASE_USER": ValueRef(literal=role),
            "POSTGRES_PASSWORD": ValueRef(secret_ref=f"{path}#password"),
            "DATABASE_PASSWORD": ValueRef(secret_ref=f"{path}#password"),
            "DATABASE_URL": ValueRef(secret_ref=f"{path}#uri"),
            "POSTGRES_SSL_MODE": ValueRef(literal="require"),
        }
    )


def _retained_password(secret: dict, *, secret_name: str, namespace: str, labels: dict, role: str) -> str:
    meta = secret.get("metadata", {})
    if (
        meta.get("name") != secret_name
        or meta.get("namespace") != namespace
        or any(meta.get("labels", {}).get(key) != value for key, value in labels.items())
    ):
        raise ValueError("CNPG slice credential Secret is not owned by this slice")
    values = dict(secret.get("stringData", {}))
    for key, value in secret.get("data", {}).items():
        try:
            values[key] = base64.b64decode(value, validate=True).decode()
        except (ValueError, UnicodeError, TypeError):
            raise ValueError("CNPG slice credential Secret is malformed") from None
    if values.get("username") != role or not isinstance(values.get("password"), str) or not values["password"]:
        raise ValueError("CNPG slice retained credentials are incompatible")
    return values["password"]


def _live_parent(driver: Any, spec: SliceSpec, parsed: ParsedHandle) -> dict[str, Any]:
    parent = driver._config.cluster_driver.get_manifest(
        parsed.cluster_id, parsed.namespace, "postgresql.cnpg.io/v1/Cluster", parsed.name
    )
    if not parent or not isinstance(parent.get("spec"), dict):
        raise ValueError("CNPG slice parent is unavailable")
    parent_meta = parent.get("metadata", {})
    parent_labels = parent_meta.get("labels", {})
    if (
        parent_meta.get("name") != parsed.name
        or parent_meta.get("namespace") != parsed.namespace
        or parent_labels.get("astrolift.io/managed-by") != "platform"
        or not parent_meta.get("uid")
        or not parent_meta.get("resourceVersion")
    ):
        raise ValueError("CNPG slice parent locator or platform ownership disagrees")
    for key in ("astrolift.io/organization", "astrolift.io/app"):
        if not spec.labels.get(key) or parent_labels.get(key) != spec.labels[key]:
            raise ValueError("CNPG slice parent owner does not match")
    return parent


def provision_slice(driver: Any, spec: SliceSpec) -> SliceResult:
    config = driver._config
    if config.cluster_driver is None or config.secrets_backend is None:
        raise ValueError("CNPG slicing requires live cluster and secrets drivers")
    parsed, host, database, role, secret_name, path = _identity(driver, spec)
    parent = _live_parent(driver, spec, parsed)
    parent_meta = parent["metadata"]
    labels = {
        "app.kubernetes.io/managed-by": "astrolift",
        "ai.astrolift/organization-id": spec.organization_id,
        "ai.astrolift/app-id": spec.app_id,
        "ai.astrolift/environment-id": spec.environment_id,
        "ai.astrolift/managed-service-id": spec.parent.managed_service_id,
    }
    db_object = secret_name.removesuffix("-owner")
    existing_db = config.cluster_driver.get_manifest(
        parsed.cluster_id, parsed.namespace, "postgresql.cnpg.io/v1/Database", db_object
    )
    if existing_db:
        _owned_database(
            existing_db,
            name=db_object,
            namespace=parsed.namespace,
            labels=labels,
            database=database,
            role=role,
            parent=parsed.name,
        )
    existing_roles = (parent["spec"].get("managed") or {}).get("roles", [])
    for entry in existing_roles:
        if entry.get("name") == role and entry.get("passwordSecret", {}).get("name") != secret_name:
            raise ValueError("CNPG slice role is already managed with different credentials")
    retained = config.cluster_driver.get_manifest(parsed.cluster_id, parsed.namespace, "v1/Secret", secret_name)
    password = (
        _retained_password(retained, secret_name=secret_name, namespace=parsed.namespace, labels=labels, role=role)
        if retained
        else None
    )
    payload = config.secrets_backend.get(path)
    if payload is not None:
        if (
            not isinstance(payload, dict)
            or payload.get("username") != role
            or payload.get("database") != database
            or payload.get("host") != host
            or not isinstance(payload.get("password"), str)
            or not payload["password"]
        ):
            raise ValueError("CNPG slice stored credentials are incompatible")
        if password is not None and password != payload["password"]:
            raise ValueError("CNPG slice credential stores disagree")
        password = payload["password"]
    password = password or secrets.token_urlsafe(40)
    uri = (
        f"postgresql://{quote(role, safe='')}:{quote(password, safe='')}@{host}:5432/"
        f"{quote(database, safe='')}?sslmode=require"
    )
    expected = {"username": role, "password": password, "database": database, "host": host, "port": "5432", "uri": uri}
    if payload is not None and payload != expected:
        raise ValueError("CNPG slice stored credential envelope is incompatible")
    if payload is None:
        config.secrets_backend.upsert(path, expected)
        if config.secrets_backend.get(path) != expected:
            raise ValueError("CNPG slice credential write could not be verified")
    secret = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": secret_name, "namespace": parsed.namespace, "labels": labels},
        "type": "kubernetes.io/basic-auth",
        "stringData": {"username": role, "password": password},
    }
    if retained is None:
        created = config.cluster_driver.apply_manifests(parsed.cluster_id, parsed.namespace, [secret], create_only=True)
        if created.errors:
            raise RuntimeError("CNPG slice credential creation failed; retry after verifying ownership")
    managed = dict(parent["spec"].get("managed") or {})
    roles = [dict(item) for item in managed.get("roles", []) if isinstance(item, dict) and item.get("name") != role]
    roles.append({"name": role, "ensure": "present", "login": True, "passwordSecret": {"name": secret_name}})
    managed["roles"] = roles
    parent_patch = {
        "apiVersion": parent["apiVersion"],
        "kind": "Cluster",
        "metadata": {key: value for key, value in parent_meta.items() if key != "managedFields"},
        "spec": {**parent["spec"], "managed": managed},
    }
    db_object = secret_name.removesuffix("-owner")
    database_manifest = {
        "apiVersion": "postgresql.cnpg.io/v1",
        "kind": "Database",
        "metadata": {"name": db_object, "namespace": parsed.namespace, "labels": labels},
        "spec": {"name": database, "owner": role, "cluster": {"name": parsed.name}, "databaseReclaimPolicy": "delete"},
    }
    result = config.cluster_driver.apply_manifests(parsed.cluster_id, parsed.namespace, [parent_patch])
    if result.errors:
        raise RuntimeError("CNPG slice role reconciliation failed")
    if existing_db is None:
        result = config.cluster_driver.apply_manifests(
            parsed.cluster_id, parsed.namespace, [database_manifest], create_only=True
        )
        if result.errors:
            raise RuntimeError("CNPG slice Database creation failed; retry after verifying ownership")
    return SliceResult(
        slice_handle=pack(
            kind="postgres_slice", cluster_id=parsed.cluster_id, namespace=parsed.namespace, name=database
        ),
        env_overrides=slice_binding(driver, spec).env_vars,
        notes=(
            "CNPG role/database reconciliation accepted; operator readiness is not certified by apply acknowledgement."
        ),
    )


def _owned_database(
    obj: dict[str, Any],
    *,
    name: str,
    namespace: str,
    labels: dict[str, str],
    database: str,
    role: str,
    parent: str,
) -> None:
    meta, spec = obj.get("metadata", {}), obj.get("spec", {})
    if (
        meta.get("name") != name
        or meta.get("namespace") != namespace
        or any(meta.get("labels", {}).get(key) != value for key, value in labels.items())
        or spec.get("name") != database
        or spec.get("owner") != role
        or spec.get("cluster", {}).get("name") != parent
        or spec.get("databaseReclaimPolicy") != "delete"
    ):
        raise ValueError("CNPG slice Database is not an owned disposable slice")


def deprovision_slice(driver: Any, spec: SliceSpec, slice_handle: str) -> bool:
    config = driver._config
    if config.cluster_driver is None:
        raise ValueError("CNPG slice removal requires a live cluster driver")
    parsed, _host, database, role, secret_name, _path = _identity(driver, spec)
    child = unpack(slice_handle)
    if (
        child.kind != "postgres_slice"
        or child.cluster_id != parsed.cluster_id
        or child.namespace != parsed.namespace
        or child.name != database
    ):
        raise ValueError("CNPG slice removal handle does not match its consumer and parent")
    _live_parent(driver, spec, parsed)
    labels = {
        "app.kubernetes.io/managed-by": "astrolift",
        "ai.astrolift/organization-id": spec.organization_id,
        "ai.astrolift/app-id": spec.app_id,
        "ai.astrolift/environment-id": spec.environment_id,
        "ai.astrolift/managed-service-id": spec.parent.managed_service_id,
    }
    db_name = secret_name.removesuffix("-owner")
    db = config.cluster_driver.get_manifest(
        parsed.cluster_id, parsed.namespace, "postgresql.cnpg.io/v1/Database", db_name
    )
    secret = config.cluster_driver.get_manifest(parsed.cluster_id, parsed.namespace, "v1/Secret", secret_name)
    if db:
        _owned_database(
            db,
            name=db_name,
            namespace=parsed.namespace,
            labels=labels,
            database=database,
            role=role,
            parent=parsed.name,
        )
    if secret:
        _retained_password(secret, secret_name=secret_name, namespace=parsed.namespace, labels=labels, role=role)
    if db:
        result = config.cluster_driver.delete_manifests(parsed.cluster_id, parsed.namespace, [db])
        if (
            result.errors
            or config.cluster_driver.get_manifest(
                parsed.cluster_id, parsed.namespace, "postgresql.cnpg.io/v1/Database", db_name
            )
            is not None
        ):
            return False
    if secret:
        result = config.cluster_driver.delete_manifests(parsed.cluster_id, parsed.namespace, [secret])
        if (
            result.errors
            or config.cluster_driver.get_manifest(parsed.cluster_id, parsed.namespace, "v1/Secret", secret_name)
            is not None
        ):
            return False
    # Preserve the portable credential envelope for retries/audit; do not rotate
    # a previously issued identity or mutate the parent's other managed roles.
    return True
