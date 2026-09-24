"""Secret references typed into a managed service's config (#1921).

A driver mints most of a service's binding refs from the instance's own
identity. Some it does not mint: it copies a config field such as
``password_secret_ref`` into the binding, or into a volume's ``secret_refs``,
or reads the secret itself to set a broker password or join a directory.
Whoever configures the service types that value, and on a cluster shared
between orgs the store it names is every tenant's store. So a config secret
ref is held to the org namespace an agent secret ref is held to. It is checked
where a config is written (provision, update, manifest persist), again before
a driver runs, again when a binding row is written, and again when a stored
row is resolved into a pod.
"""

from __future__ import annotations

import re
from typing import Any

from astrolift_dispatch.agent_secrets import (
    ORG_SECRET_ROOTS,
    SecretRefNamespaceError,
    in_org_secret_namespace,
)

# A config key names a secret in the platform's store when it is spelled
# ``*_secret_ref``, ``*_secret_refs``, ``*_secret_arn`` or ``*_secret_arns``
# (``password_secret_ref``, ``body_secret_refs``, ``secret_arn``), which is the
# convention every driver follows, or is one of the few fields that name a
# secret without following it. A string held directly by such a key is a ref,
# and so is a string in a list or map it holds. The Kubernetes ``secretRef`` and
# ``secretKeyRef`` spellings are not matched on purpose: they name a Secret in
# the workload's own namespace, not a location in the platform's store.
_SECRET_REF_KEY_SUFFIXES = ("secret_ref", "secret_refs", "secret_arn", "secret_arns")
_SECRET_REF_KEYS = frozenset(
    {
        # k8s_native object_store existing_s3: a bundle path, copied into the
        # binding as ``<bundle>#<field>``.
        "credential_bundle",
        # AWS FSx for Windows, inside the native self-managed directory block:
        # FSx reads the secret and sends it to the directory the same block
        # names.
        "DomainJoinServiceAccountSecret",
    }
)
# MSK SCRAM secrets must be named ``AmazonMSK_*``, so no location in an org
# namespace can hold one. Associating one with a cluster discloses nothing: MSK
# never returns the value, and the credentials a workload uses come from
# ``username_secret_ref`` and ``password_secret_ref``, which are checked.
_WAIVED_SECRET_REF_KEYS = frozenset({"scram_secret_arns"})

_SECRETS_MANAGER_ARN_RE = re.compile(
    r"arn:aws[a-z-]*:secretsmanager:[a-z0-9-]+:[0-9]{12}:secret:(?P<name>[A-Za-z0-9/_+=.@-]+)"
)
_STORE_SCHEMES = ("sm:", "ssm:")


def _is_secret_ref_key(key: Any) -> bool:
    name = str(key)
    if name in _WAIVED_SECRET_REF_KEYS:
        return False
    return name in _SECRET_REF_KEYS or name.endswith(_SECRET_REF_KEY_SUFFIXES)


def config_secret_refs(config: Any) -> list[tuple[str, str]]:
    """``(path, ref)`` for every secret reference ``config`` carries, in
    document order. ``path`` reads like ``users[0].password_secret_ref``."""
    found: list[tuple[str, str]] = []

    def walk(value: Any, path: str, held_by_ref_key: bool) -> None:
        if isinstance(value, dict):
            # A ref key holding ``{"secret_ref": ..., "field": ...}`` holds one
            # reference object, not a map of refs, so ``field`` is not a ref.
            as_map = held_by_ref_key and not any(_is_secret_ref_key(key) for key in value)
            items = [
                (f"{path}.{key}" if path else str(key), item, _is_secret_ref_key(key), as_map)
                for key, item in value.items()
            ]
        elif isinstance(value, list):
            items = [(f"{path}[{index}]", item, False, held_by_ref_key) for index, item in enumerate(value)]
        else:
            return
        for child, item, key_is_ref, entry_is_ref in items:
            if isinstance(item, str):
                if (key_is_ref or entry_is_ref) and item.strip():
                    found.append((child, item))
            else:
                walk(item, child, key_is_ref)

    walk(config, "", False)
    return found


def _scoped(ref: str, organization) -> bool:
    """Whether a config secret ref names a location in the org's namespace.

    A Secrets Manager ARN is accepted when the name it carries is: an AWS API
    that takes a secret (RDS Proxy auth, a directory join) takes nothing else,
    and that name is what confines it. The org guid in it is a segment no
    store the platform writes for another org can carry.
    """
    candidate = ref.strip().removeprefix("secret://")
    for scheme in _STORE_SCHEMES:
        if candidate.startswith(scheme):
            candidate = candidate[len(scheme) :]
            break
    base, separator, field = candidate.partition("#")
    arn = _SECRETS_MANAGER_ARN_RE.fullmatch(base.lstrip("/"))
    if arn:
        candidate = f"{arn.group('name')}{separator}{field}"
    return in_org_secret_namespace(candidate, organization=organization, roots=ORG_SECRET_ROOTS)


def _namespace_message(organization) -> str:
    guid = organization.guid
    return (
        "outside this organization's secret namespace; a managed-service secret must live "
        f"under services/{guid}/, agents/{guid}/ or agent-bundles/{guid}/"
    )


def assert_config_secret_refs_scoped(config: Any, *, organization) -> None:
    """Raise :class:`SecretRefNamespaceError` naming the first secret
    reference in ``config`` that sits outside ``organization``'s namespace."""
    for path, ref in config_secret_refs(config):
        if not _scoped(ref, organization):
            raise SecretRefNamespaceError(f"config.{path} {ref!r} is {_namespace_message(organization)}")


def service_organization(service) -> Any:
    """The organization that owns ``service``, through its app or project."""
    owner = service.registered_app if service.registered_app_id else service.project
    return owner.organization


def _canonical(ref: str) -> str:
    """``ref`` without a ``#field`` selector, reference or store scheme,
    leading slashes or install root, so a copy is recognized in any spelling."""
    path = ref.strip().partition("#")[0].removeprefix("secret://")
    for scheme in _STORE_SCHEMES:
        if path.startswith(scheme):
            path = path[len(scheme) :]
            break
    return path.lstrip("/").removeprefix("astrolift/")


def _config_strings(service) -> set[str]:
    out: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, str):
            canonical = _canonical(value)
            if canonical:
                out.add(canonical)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(service.config)
    walk(service.applied_config)
    return out


def managed_binding_ref_reason(service, ref: str) -> str | None:
    """Why ``ref``, a secret ref on one of ``service``'s binding rows or IAM
    grants, must not be resolved, or ``None``.

    A ref the driver minted from the instance's identity resolves wherever the
    driver put it, unless the service's config, desired or applied, names a
    secret outside the owning org's namespace: then nothing the service binds
    can be trusted, because a driver may have turned that value into this ref
    (Amazon MQ grants on an ARN built from it, RDS Proxy echoes the ARN AWS
    stored for it). A ref that is a verbatim copy of any config string is held
    to the namespace too, whatever field it came from. A row written before
    these checks existed fails closed here instead of resolving.
    """
    typed = [row for config in (service.config, service.applied_config) for row in config_secret_refs(config)]
    canonical = _canonical(ref)
    copied = bool(canonical) and canonical in _config_strings(service)
    if not typed and not copied:
        return None
    organization = service_organization(service)
    for path, value in typed:
        if not _scoped(value, organization):
            return (
                f"the {service.kind} service's config.{path} {value!r} is {_namespace_message(organization)}"
            )
    if copied and not _scoped(ref, organization):
        return f"{ref!r} comes from the {service.kind} service's config and is {_namespace_message(organization)}"
    return None
