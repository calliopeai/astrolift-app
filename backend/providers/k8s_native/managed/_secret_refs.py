"""Tenant Secret references in a namespace shared between tenants (#1959).

A driver whose install sets ``namespace`` runs every tenant's workloads in
that one namespace, where a Secret named in tenant config resolves to
whichever tenant's (or the platform's) Secret has that name. There is no
per-Secret owner to check, so such references are refused there; with the
default per-app namespace a tenant can only reach its own Secrets.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable


def secret_names_in(value: Any) -> set[str]:
    """Every Secret a pod-spec-like ``value`` names: env and envFrom refs, secret volumes, pull secrets."""
    names: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ("secretRef", "secretKeyRef") and isinstance(item, dict) and item.get("name"):
                names.add(str(item["name"]))
            elif key == "secret" and isinstance(item, dict) and item.get("secretName"):
                names.add(str(item["secretName"]))
            elif key in ("imagePullSecrets", "image_pull_secrets") and isinstance(item, list):
                names.update(str(row.get("name") if isinstance(row, dict) else row) for row in item if row)
            names |= secret_names_in(item)
    elif isinstance(value, list):
        for item in value:
            names |= secret_names_in(item)
    return names


def refuse_shared_namespace_secrets(shared_namespace: str | None, value: Any, *, extra: Iterable[str] = ()) -> None:
    """Raise ``ValueError`` when ``value`` names a Secret and the namespace is shared."""
    if not shared_namespace:
        return
    names = secret_names_in(value) | {str(name) for name in extra if name}
    if names:
        raise ValueError(
            f"Secret references ({', '.join(sorted(names))}) are not allowed: this driver runs in the "
            f"namespace {shared_namespace!r}, shared between tenants, where a Secret name can resolve "
            "to another tenant's Secret; use the platform's secret bindings instead"
        )
