"""Secret references typed into a managed service's config (#1921).

A driver mints most of a service's binding refs from the instance's own
identity. Some it does not mint: it copies a config field such as
``password_secret_ref`` into the binding, or into a volume's ``secret_refs``,
reads the secret itself to set a broker password or join a directory, or hands
a Google Secret Manager id to Google, which reads it with a Google service
identity. Whoever configures the service types that value, and on a cluster
shared between orgs the store it names is every tenant's store. It is checked
where a config is written (provision, update, manifest persist, adopt), again
before a driver runs, again when a binding row is written, and again when a
stored row is resolved into a pod.

Every secret a config names must sit under ``services/<org guid>/<owner guid>/``,
where the owner is the app the service belongs to, or the project for a project
service. The org alone is too wide. Changing an app's service config takes
APP_UPDATE on that app, while reading a secret through the agent surface takes
SECRET_READ and elevation. If one app's config could name another app's
``services/`` secret, or an agent secret under ``agents/<org guid>/``, the
driver would copy it into this app's binding and its pods would read what the
caller cannot.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from astrolift_dispatch.agent_secrets import SecretRefNamespaceError, in_org_secret_namespace

MANAGED_SERVICE_SECRET_ROOTS = ("services",)

# A config key names a secret in the platform's store when it ends in
# ``secret_ref`` or ``secret_refs`` (the Astrolift convention:
# ``password_secret_ref``, ``body_secret_refs``) or names a Secrets Manager ARN
# the way either Astrolift or AWS spells one (``secret_arn``, ``secret_arns``,
# the native ``SecretArn`` and ``SecretARN``), in any case, or is one of the few
# fields that name a secret some other way. A string held directly by such a
# key is a ref, and so is a string in a list or map it holds. ``Ref`` is matched
# only after a separator: the Kubernetes ``secretRef`` spelling, and a CRD's
# ``passwordSecretRef``, name a Secret in the workload's own namespace, not a
# location in the platform's store.
_SECRET_REF_KEY_RE = re.compile(r"(secret[_-]refs?|secret[_-]?arns?)$", re.IGNORECASE)
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
_SECRET_REFERENCE_SCHEME = "secret://"

# Google Secret Manager references a config hands to Google, which reads the
# secret with its own service identity rather than through the platform's
# secrets driver: a Cloud Functions ``secret_environment[]`` or
# ``secret_volumes[]`` entry names a secret id (and optionally the project
# holding it), and a Managed Kafka Connect cluster's ``secret_paths[]`` names
# ``projects/<p>/secrets/<id>/versions/<n>``. The camelCase API spellings are
# matched too, so a raw-field passthrough cannot carry one past the walker.
_GCP_SECRET_ENTRY_KEYS = frozenset(
    {"secret_environment", "secret_volumes", "secretEnvironmentVariables", "secretVolumes"}
)
_GCP_SECRET_VERSION_KEYS = frozenset({"secret_paths", "secretPaths"})
_GCP_SECRET_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,255}")
_GCP_SECRET_VERSION_RE = re.compile(r"projects/(?P<project>[^/]+)/secrets/(?P<secret>[^/]+)/versions/[0-9]+")


def _is_secret_ref_key(key: Any) -> bool:
    name = str(key)
    if name in _WAIVED_SECRET_REF_KEYS:
        return False
    return name in _SECRET_REF_KEYS or bool(_SECRET_REF_KEY_RE.search(name))


def config_secret_refs(config: Any) -> list[tuple[str, str]]:
    """``(path, ref)`` for every platform-store secret reference ``config``
    carries, in document order. ``path`` reads like
    ``users[0].password_secret_ref``. Google Secret Manager references are
    :func:`gcp_secret_refs`."""
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


@dataclass(frozen=True)
class GcpSecretRef:
    """A Google Secret Manager secret a config hands to Google to read."""

    path: str
    ref: str
    secret_id: str
    """Empty when the entry does not spell a secret id, which fails the check."""
    project: str
    """The project the entry names, or empty when it names none: then Google
    reads the secret from the consuming resource's own project."""


def gcp_secret_refs(config: Any) -> list[GcpSecretRef]:
    """Every Google Secret Manager secret ``config`` names for Google to read,
    in document order."""
    found: list[GcpSecretRef] = []

    def entry(value: Any, path: str) -> None:
        if not isinstance(value, dict):
            found.append(GcpSecretRef(path=path, ref=str(value), secret_id="", project=""))
            return
        secret = value.get("secret")
        if secret is None or (isinstance(secret, str) and not secret.strip()):
            return
        project = value.get("project_id", value.get("projectId"))
        found.append(
            GcpSecretRef(
                path=f"{path}.secret",
                ref=str(secret),
                secret_id=secret.strip() if isinstance(secret, str) else "",
                project="" if project is None else str(project).strip(),
            )
        )

    def version(value: Any, path: str) -> None:
        text = value.strip() if isinstance(value, str) else ""
        if isinstance(value, str) and not text:
            return
        match = _GCP_SECRET_VERSION_RE.fullmatch(text)
        found.append(
            GcpSecretRef(
                path=path,
                ref=str(value),
                secret_id=match.group("secret") if match else "",
                project=match.group("project") if match else "",
            )
        )

    def walk(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                child = f"{path}.{key}" if path else str(key)
                if key in _GCP_SECRET_ENTRY_KEYS and isinstance(item, list):
                    for index, value_item in enumerate(item):
                        entry(value_item, f"{child}[{index}]")
                elif key in _GCP_SECRET_VERSION_KEYS and isinstance(item, list):
                    for index, value_item in enumerate(item):
                        version(value_item, f"{child}[{index}]")
                else:
                    walk(item, child)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]")

    walk(config, "")
    return found


def service_owner(service) -> Any:
    """The app that owns ``service``, or the project for a project service."""
    return service.registered_app if service.registered_app_id else service.project


def service_organization(service) -> Any:
    """The organization that owns ``service``, through its app or project."""
    return service_owner(service).organization


def owner_secret_namespace(owner) -> str:
    """``services/<org guid>/<owner guid>/``: where every secret a managed
    service of ``owner`` (an app or a project) names must sit."""
    return f"services/{owner.organization.guid}/{owner.guid}/"


def _namespace_message(owner) -> str:
    if owner is None:
        return "outside every secret namespace: the service has no owning app or project"
    return (
        "outside the secret namespace of the app or project that owns this service; a "
        f"managed-service secret must live under {owner_secret_namespace(owner)}"
    )


def _store_ref_reason(ref: str, owner) -> str | None:
    """Why the platform-store ref ``ref`` must not be named by a config of a
    service ``owner`` owns (a phrase starting "is"), or ``None``.

    ``ref`` is judged exactly as the driver reads it. A Secrets Manager ARN is
    accepted when the name it carries is in the namespace: an AWS API that
    takes a secret (RDS Proxy auth, a directory join) takes nothing else, and
    the org and owner guids in the name confine it.
    """
    candidate = ref.strip()
    if owner is None:
        return f"is {_namespace_message(owner)}"
    if candidate.startswith(_SECRET_REFERENCE_SCHEME):
        # A driver reads its config as it is, and none strips the reference
        # scheme: AWS would look up ``astrolift/secret://...``.
        return (
            "is a secret:// reference, but a driver reads this field as a store location; drop the "
            f"scheme and keep it under {owner_secret_namespace(owner)}"
        )
    scheme = next((prefix for prefix in _STORE_SCHEMES if candidate.startswith(prefix)), "")
    base, separator, field = candidate[len(scheme) :].partition("#")
    arn = _SECRETS_MANAGER_ARN_RE.fullmatch(base.lstrip("/"))
    if arn:
        candidate = f"{scheme}{arn.group('name')}{separator}{field}"
    if in_org_secret_namespace(
        candidate, organization=owner.organization, roots=MANAGED_SERVICE_SECRET_ROOTS, owner=owner
    ):
        return None
    return f"is {_namespace_message(owner)}"


@dataclass(frozen=True)
class GcpSecretStore:
    """The project and secret id prefix a GCP cluster's secrets driver uses."""

    project_id: str
    secret_id_prefix: str


def gcp_secret_store(cluster) -> GcpSecretStore | None:
    """The Secret Manager project and id prefix the secrets driver of the GCP
    cluster ``cluster`` files platform secrets under, or ``None`` when the
    cluster is not a GCP cluster with a project. Read from that driver's own
    config builder, so the check and the store cannot disagree about either."""
    if cluster is None or getattr(getattr(cluster, "provider_plugin", None), "slug", "") != "gcp":
        return None
    from core.app_deploy import AppDeployError, _config_for_capability_uncredentialed

    try:
        config = _config_for_capability_uncredentialed("gcp", cluster, "secrets")
    except AppDeployError:
        return None
    return GcpSecretStore(project_id=str(config.project_id), secret_id_prefix=str(config.secret_id_prefix))


def _gcp_ref_reason(item: GcpSecretRef, *, owner, store: GcpSecretStore | None) -> str | None:
    """Why the Google Secret Manager reference ``item`` must not be handed to
    Google, or ``None``.

    It passes when it names the install's project, or none (the consuming
    resource lives there), and a secret id the GCP secrets driver files under
    the owner's namespace: the physical id of ``services/<org guid>/<owner
    guid>/<name>``, mapped by the driver's own ``secret_id_for``.
    """
    where = f"config.{item.path} {item.ref!r}"
    if owner is None:
        return f"{where} is {_namespace_message(owner)}"
    if store is None:
        return (
            f"{where} names a Google Secret Manager secret, but the service's cluster has no GCP "
            "secrets project to hold one"
        )
    if item.project not in ("", store.project_id):
        return (
            f"{where} names project {item.project!r}; a Google Secret Manager secret a managed "
            f"service names must be in the install project {store.project_id!r}"
        )
    from gcp.secrets import secret_id_for

    root = secret_id_for(owner_secret_namespace(owner), prefix=store.secret_id_prefix)
    if (
        _GCP_SECRET_ID_RE.fullmatch(item.secret_id)
        and item.secret_id.startswith(root)
        and item.secret_id != root
    ):
        return None
    return f"{where} is {_namespace_message(owner)}; in Google Secret Manager its id must start {root!r}"


def unscoped_config_secret_refs(config: Any, *, owner, cluster) -> list[tuple[str, str, str]]:
    """``(path, ref, reason)`` for each secret reference in ``config`` that
    sits outside the namespace of ``owner`` (the app or project owning the
    service). ``cluster`` is the service's cluster; a Google Secret Manager
    reference is judged against its secrets project and id prefix."""
    found = []
    for path, ref in config_secret_refs(config):
        reason = _store_ref_reason(ref, owner)
        if reason is not None:
            found.append((path, ref, f"config.{path} {ref!r} {reason}"))
    native = gcp_secret_refs(config)
    if native:
        store = gcp_secret_store(cluster)
        for item in native:
            reason = _gcp_ref_reason(item, owner=owner, store=store)
            if reason is not None:
                found.append((item.path, item.ref, reason))
    return found


def assert_config_secret_refs_scoped(config: Any, *, owner, cluster) -> None:
    """Raise :class:`SecretRefNamespaceError` naming the first secret
    reference in ``config`` outside ``owner``'s namespace."""
    for _path, _ref, reason in unscoped_config_secret_refs(config, owner=owner, cluster=cluster):
        raise SecretRefNamespaceError(reason)


def _canonical(ref: str) -> str:
    """``ref`` without a ``#field`` selector, reference or store scheme,
    leading slashes or install root, so a copy is recognized in any spelling."""
    path = ref.strip().partition("#")[0].removeprefix(_SECRET_REFERENCE_SCHEME)
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
    secret outside its owner's namespace: then nothing the service binds can be
    trusted, because a driver may have turned that value into this ref (Amazon
    MQ grants on an ARN built from it, RDS Proxy echoes the ARN AWS stored for
    it). A ref that is a verbatim copy of any config string is held to the
    namespace too, whatever field it came from. A row written before these
    checks existed fails closed here instead of resolving.
    """
    configs = (service.config, service.applied_config)
    native = [item for config in configs for item in gcp_secret_refs(config)]
    typed = bool(native) or any(config_secret_refs(config) for config in configs)
    canonical = _canonical(ref)
    copied = bool(canonical) and canonical in _config_strings(service)
    if not typed and not copied:
        return None
    owner = service_owner(service)
    cluster = service.effective_cluster if native else None
    for config in configs:
        for _path, _value, reason in unscoped_config_secret_refs(config, owner=owner, cluster=cluster):
            return f"the {service.kind} service's {reason}"
    if copied:
        reason = _store_ref_reason(ref, owner)
        if reason is not None:
            return f"{ref!r} comes from the {service.kind} service's config and {reason}"
    return None
