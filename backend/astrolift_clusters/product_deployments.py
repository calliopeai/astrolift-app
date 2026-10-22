"""Find the Calliope AI products the Deployment Manager runs on managed clusters (#2465).

The installer labels and annotates each product namespace it creates
(contract version 1, calliope-installer
``docs/contracts/astrolift-product-deployments.md``; design:
``docs/design/product-deployments-2465.md``). This module is the reader: a
scheduled scan lists the labelled namespaces on every managed cluster,
keeps one ``ProductDeployment`` row per namespace, and emits
``product_deployment.changed`` when a product appears, goes, changes health
or changes images.

Read-only against the clusters: the scan lists namespaces and reads
workload health, and never writes there. The labels drive display only.

Reader rules (design section 4):

* A matching namespace that carries ``astrolift.io/control-plane-scope=true``
  is Astrolift's own and is skipped, as is one being deleted.
* Contract majors N and N-1 are read. Any other is listed with its kind,
  deployment and namespace only.
* Unknown annotations are ignored. A malformed one empties its own field
  and is recorded in ``last_scan_error``; the row still lists.
* Rows a successful list no longer returns get ``gone_at``. A list that
  fails changes nothing but ``last_scan_error``.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import re
from collections import defaultdict
from datetime import datetime
from functools import partial
from typing import Any
from urllib.parse import urlsplit

from django.db import transaction
from django.utils import timezone

from astrolift_clusters.heartbeat_status import HeartbeatStatus, resolve
from astrolift_clusters.models import ProductDeployment, TenantCluster
from core.control_plane_rbac import SCOPE_LABEL
from core.events import Event
from core.tenancy import TenantContext, tenant_context

log = logging.getLogger(__name__)

PRODUCT_DEPLOYMENT_CHANGED = "product_deployment.changed"

CONTRACT_LABEL = "calliope.ai/product-contract"
PRODUCT_LABEL = "calliope.ai/product"
DEPLOYMENT_LABEL = "calliope.ai/deployment"
MANAGED_BY_LABEL = "calliope.ai/managed-by"
IMAGES_ANNOTATION = "calliope.ai/images"
ENDPOINTS_ANNOTATION = "calliope.ai/endpoints"
WORKLOADS_ANNOTATION = "calliope.ai/workloads"
INTEGRATIONS_ANNOTATION = "calliope.ai/integrations"
VERSION_ANNOTATION = "calliope.ai/version"

PRODUCTS = frozenset(ProductDeployment.Kind.values)
PRODUCT_SELECTOR = f"{CONTRACT_LABEL},{PRODUCT_LABEL} in ({','.join(sorted(PRODUCTS))})"

# Majors N and N-1. Contract 1 is the first, so there is no N-1 yet.
SUPPORTED_CONTRACT_VERSIONS = frozenset({1})

_CONTRACT_VERSION = re.compile(r"[0-9]{1,9}")
_FLAG = re.compile(r"[a-z0-9][a-z0-9-]*")
_MAX_VERSION_LENGTH = ProductDeployment._meta.get_field("product_version").max_length
_MAX_ERROR_LENGTH = 1000


@dataclasses.dataclass(frozen=True)
class ProductNamespace:
    """What one product namespace's labels and annotations say."""

    namespace: str
    uid: str
    kind: str
    deployment_name: str
    managed_by: str
    contract_version: int
    contract_supported: bool
    product_version: str = ""
    images: dict[str, str] = dataclasses.field(default_factory=dict)
    endpoints: dict[str, str] = dataclasses.field(default_factory=dict)
    workloads: tuple[str, ...] = ()
    integrations: tuple[str, ...] = ()
    problems: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class ClusterScan:
    """One cluster's scan: whether its namespaces were listed, and how many
    product deployments changed."""

    listed: bool
    changes: int = 0
    error: str = ""


# ---- reading the contract -----------------------------------------------


def read_product_namespace(namespace: dict[str, Any]) -> ProductNamespace | None:
    """The contract's view of one listed namespace, or ``None`` when it is
    not a product namespace the scan should list."""
    name = namespace.get("name") or ""
    labels = namespace.get("labels") or {}
    annotations = namespace.get("annotations") or {}
    kind = labels.get(PRODUCT_LABEL, "")
    if not name or kind not in PRODUCTS or CONTRACT_LABEL not in labels:
        return None
    # Astrolift labels every namespace it writes under the minimal RBAC
    # contract; nothing it created may claim to be a product.
    if labels.get(SCOPE_LABEL) == "true":
        return None
    # A namespace being deleted is a teardown in progress, not a product.
    if namespace.get("phase") == "Terminating":
        return None

    problems: list[str] = []
    raw_version = labels[CONTRACT_LABEL]
    if _CONTRACT_VERSION.fullmatch(raw_version):
        contract_version = int(raw_version)
    else:
        contract_version = 0
        problems.append(f"{CONTRACT_LABEL} {raw_version!r} is not a version number")
    identity = {
        "namespace": name,
        "uid": namespace.get("uid") or "",
        "kind": kind,
        "deployment_name": labels.get(DEPLOYMENT_LABEL, ""),
        "contract_version": contract_version,
    }
    if contract_version not in SUPPORTED_CONTRACT_VERSIONS:
        return ProductNamespace(**identity, managed_by="", contract_supported=False, problems=tuple(problems))

    return ProductNamespace(
        **identity,
        managed_by=labels.get(MANAGED_BY_LABEL, ""),
        contract_supported=True,
        product_version=_product_version(annotations, problems),
        images=_string_map(annotations, IMAGES_ANNOTATION, problems),
        endpoints=_string_map(annotations, ENDPOINTS_ANNOTATION, problems, urls=True),
        workloads=_workloads(annotations, problems),
        integrations=(
            _integrations(annotations, problems) if kind == ProductDeployment.Kind.ZENTINELLE else ()
        ),
        problems=tuple(problems),
    )


def _string_map(
    annotations: dict[str, str], key: str, problems: list[str], *, urls: bool = False
) -> dict[str, str]:
    raw = annotations.get(key)
    if raw is None:
        return {}
    try:
        value = json.loads(raw)
    except ValueError:
        problems.append(f"{key} is not JSON")
        return {}
    if not isinstance(value, dict) or not all(
        isinstance(k, str) and k and isinstance(v, str) and v for k, v in value.items()
    ):
        problems.append(f"{key} is not an object of non-empty strings")
        return {}
    if urls:
        for k, v in value.items():
            parts = urlsplit(v)
            if parts.scheme not in ("http", "https") or not parts.netloc:
                problems.append(f"{key} {k!r} is not an absolute http(s) URL")
                return {}
    return dict(sorted(value.items()))


def _workloads(annotations: dict[str, str], problems: list[str]) -> tuple[str, ...]:
    raw = annotations.get(WORKLOADS_ANNOTATION)
    if raw is None:
        return ()
    names: list[str] = []
    for entry in (part.strip() for part in raw.split(",")):
        if not entry:
            continue
        kind, slash, name = entry.partition("/")
        if not slash or not kind or not name:
            problems.append(f"{WORKLOADS_ANNOTATION} entry {entry!r} is not kind/name")
            return ()
        # Health is read from Deployments; a kind a later contract adds is skipped.
        if kind.lower() == "deployment" and name not in names:
            names.append(name)
    return tuple(names)


def _integrations(annotations: dict[str, str], problems: list[str]) -> tuple[str, ...]:
    raw = annotations.get(INTEGRATIONS_ANNOTATION)
    if raw is None:
        return ()
    flags = [part.strip() for part in raw.split(",") if part.strip()]
    if not all(_FLAG.fullmatch(flag) for flag in flags):
        problems.append(f"{INTEGRATIONS_ANNOTATION} is not a list of flags")
        return ()
    return tuple(sorted(set(flags)))


def _product_version(annotations: dict[str, str], problems: list[str]) -> str:
    raw = (annotations.get(VERSION_ANNOTATION) or "").strip()
    if len(raw) > _MAX_VERSION_LENGTH:
        problems.append(f"{VERSION_ANNOTATION} is longer than {_MAX_VERSION_LENGTH} characters")
        return ""
    return raw


# ---- what is running -----------------------------------------------------


def _repository(reference: str) -> str:
    """``reference`` without its tag or digest, with Docker Hub's implied
    registry and ``library/`` written out, so the declared reference and
    the one a kubelet reports compare equal."""
    name = reference.strip().split("@", 1)[0]
    head, slash, last = name.rpartition("/")
    name = f"{head}{slash}{last.split(':', 1)[0]}"
    registry, _, path = name.partition("/")
    if not path or ("." not in registry and ":" not in registry and registry != "localhost"):
        registry, path = "docker.io", name
    if registry in ("docker.io", "index.docker.io"):
        registry = "docker.io"
        if "/" not in path:
            path = f"library/{path}"
    return f"{registry}/{path}"


def _digest(image_id: str) -> str:
    _, at, digest = image_id.rpartition("@")
    if at:
        return digest
    return image_id if image_id.startswith("sha256:") else ""


def _observe(
    product: ProductNamespace,
    rows: list[dict[str, Any]] | None,
) -> tuple[str, list[dict[str, Any]], dict[str, list[str]]]:
    """Health, workload states and running images from the namespace's
    workload rows (``None`` when none could be read)."""
    if not product.workloads:
        return ProductDeployment.Health.UNKNOWN, [], {}
    if rows is None:
        unread = [_workload(name, found=None) for name in product.workloads]
        return ProductDeployment.Health.UNKNOWN, unread, {}

    by_name = {row["name"]: row for row in rows}
    workloads = [
        _workload(name, row=by_name[name]) if name in by_name else _workload(name, found=False)
        for name in product.workloads
    ]
    if any(not w["found"] or w["ready_replicas"] == 0 for w in workloads):
        health = ProductDeployment.Health.DOWN
    elif any(w["ready_replicas"] < w["desired_replicas"] for w in workloads):
        health = ProductDeployment.Health.DEGRADED
    else:
        health = ProductDeployment.Health.HEALTHY

    seen = [image for name in product.workloads for image in by_name.get(name, {}).get("running_images", [])]
    running: dict[str, list[str]] = {}
    for component, reference in product.images.items():
        repository = _repository(reference)
        digests = {_digest(i["image_id"]) for i in seen if _repository(i["image"]) == repository}
        running[component] = sorted(digests - {""})
    return health, workloads, running


def _workload(name: str, *, row: dict[str, Any] | None = None, found: bool | None = True) -> dict[str, Any]:
    """One declared workload. ``found`` is ``None`` when nothing could be
    read and ``False`` when the namespace has no such Deployment."""
    row = row or {}
    return {
        "name": name,
        "found": found,
        "desired_replicas": int(row.get("desired_replicas", 0)),
        "ready_replicas": int(row.get("ready_replicas", 0)),
        "restart_count_24h": int(row.get("restart_count_24h", 0)),
    }


# ---- the scan --------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class _Change:
    """One ``product_deployment.changed`` event, held until the commit."""

    guid: str
    payload: dict[str, Any]
    organization_id: int | None
    team_id: int | None
    project_id: int | None


def scan_product_deployments(*, now: datetime | None = None) -> int:
    """Scan every managed, active cluster whose heartbeat is not offline.

    Returns how many clusters' namespaces were listed. One cluster's
    failure never stops the others.
    """
    now = now or timezone.now()
    clusters = TenantCluster.objects.filter(
        lifecycle=TenantCluster.Lifecycle.MANAGED,
        is_active=True,
    ).select_related("provider_plugin")
    listed = 0
    for cluster in list(clusters):
        status = resolve(
            last_heartbeat_at=cluster.last_heartbeat_at,
            interval_seconds=cluster.heartbeat_interval_seconds,
            now=now,
        )
        if status == HeartbeatStatus.OFFLINE:
            continue
        try:
            if scan_cluster(cluster, now=now).listed:
                listed += 1
        except Exception:  # noqa: BLE001 — one cluster must not stop the sweep
            log.exception("product scan failed for cluster %s", cluster.slug)
    return listed


def scan_cluster(cluster: TenantCluster, *, now: datetime | None = None) -> ClusterScan:
    """List ``cluster``'s product namespaces and bring its rows in line."""
    from core.cluster_management import ClusterManagementError, cluster_namespaces_dispatch

    now = now or timezone.now()
    try:
        listed = cluster_namespaces_dispatch(cluster=cluster, label_selector=PRODUCT_SELECTOR)
    except ClusterManagementError as exc:
        error = _bounded(f"could not list product namespaces: {exc}")
        ProductDeployment.objects.filter(cluster=cluster, gone_at__isnull=True).exclude(
            last_scan_error=error
        ).update(last_scan_error=error, updated_at=now)
        log.warning("product scan: cluster %s: %s", cluster.slug, error)
        return ClusterScan(listed=False, error=error)

    products: dict[str, ProductNamespace] = {}
    for item in listed:
        product = read_product_namespace(item)
        if product is not None:
            products[product.namespace] = product
    rows_by_namespace, health_error = _workload_rows(cluster, list(products.values()))

    changes: list[_Change] = []
    with transaction.atomic():
        locked = TenantCluster.objects.select_for_update().filter(pk=cluster.pk).first()
        if locked is None:
            return ClusterScan(listed=True)
        existing = {
            row.namespace: row
            for row in ProductDeployment.objects.filter(cluster=locked)
            .select_related("project")
            .select_for_update(of=("self",))
        }
        for namespace, product in sorted(products.items()):
            problems = list(product.problems)
            rows = None if rows_by_namespace is None else rows_by_namespace.get(namespace)
            if product.workloads and rows is None:
                problems.append(health_error or "no workload health could be read in the namespace")
            changes.extend(_apply(locked, existing.get(namespace), product, rows, problems, now))
        for namespace, row in sorted(existing.items()):
            if namespace not in products and row.gone_at is None:
                row.gone_at = now
                row.save(update_fields=["gone_at", "updated_at", "version"])
                changes.append(_change(row, locked, ["gone"]))
        for change in changes:
            transaction.on_commit(partial(_emit, change), robust=True)
    if changes:
        log.info("product scan: cluster %s: %d change(s)", cluster.slug, len(changes))
    return ClusterScan(listed=True, changes=len(changes))


def _workload_rows(
    cluster: TenantCluster,
    products: list[ProductNamespace],
) -> tuple[dict[str, list[dict[str, Any]]] | None, str]:
    """Workload health rows by namespace, or ``None`` and why when the read failed."""
    from core.cluster_management import ClusterManagementError, cluster_workload_health_dispatch

    namespaces = sorted({p.namespace for p in products if p.workloads})
    if not namespaces:
        # Never an empty list: the driver reads that as its default namespace.
        return {}, ""
    try:
        rows = cluster_workload_health_dispatch(cluster=cluster, namespaces=namespaces)
    except ClusterManagementError as exc:
        return None, _bounded(f"could not read workload health: {exc}")
    by_namespace: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_namespace[row["namespace"]].append(row)
    return dict(by_namespace), ""


def _apply(
    cluster: TenantCluster,
    row: ProductDeployment | None,
    product: ProductNamespace,
    rows: list[dict[str, Any]] | None,
    problems: list[str],
    now: datetime,
) -> list[_Change]:
    """Create or update the row for one listed product namespace."""
    health, workloads, running = _observe(product, rows)
    values = {
        "kind": product.kind,
        "deployment_name": product.deployment_name,
        "namespace_uid": product.uid,
        "managed_by": product.managed_by,
        "contract_version": product.contract_version,
        "contract_supported": product.contract_supported,
        "product_version": product.product_version,
        "images": product.images,
        "running_images": running,
        "endpoints": product.endpoints,
        "workloads": workloads,
        "integrations": list(product.integrations),
        "health": health,
        "last_scan_error": _bounded("; ".join(problems)),
        "last_seen_at": now,
        "gone_at": None,
    }
    changes: list[_Change] = []
    if row is not None and (row.kind, row.deployment_name) != (product.kind, product.deployment_name):
        # Another product or install holds the namespace now. A reinstall of
        # the same one keeps its row; this one must not inherit the project.
        if row.gone_at is None:
            row.gone_at = now
            changes.append(_change(row, cluster, ["gone"]))
        row.deleted_at = now
        row.save(update_fields=["gone_at", "deleted_at", "updated_at", "version"])
        row = None
    if row is None:
        row = ProductDeployment.objects.create(
            cluster=cluster, namespace=product.namespace, first_seen_at=now, **values
        )
        return [*changes, _change(row, cluster, ["appeared"])]

    what: list[str] = []
    if row.gone_at is not None:
        what.append("appeared")
    else:
        if row.health != health:
            what.append("health")
        if row.images != product.images or row.running_images != running:
            what.append("images")
    previous_health = row.health
    for field, value in values.items():
        setattr(row, field, value)
    row.save(update_fields=[*values, "updated_at", "version"])
    if what:
        changes.append(_change(row, cluster, what, previous_health=previous_health))
    return changes


def _change(
    row: ProductDeployment,
    cluster: TenantCluster,
    what: list[str],
    *,
    previous_health: str = "",
) -> _Change:
    organization_id, team_id, project_id = _owner(row, cluster)
    payload: dict[str, Any] = {
        "changes": what,
        "product_deployment_id": str(row.guid),
        "kind": row.kind,
        "deployment_name": row.deployment_name,
        "namespace": row.namespace,
        "cluster_id": str(cluster.guid),
        "cluster_slug": cluster.slug,
        "health": str(row.health),
        "severity": _severity(row.health, what),
    }
    if "health" in what:
        payload["previous_health"] = previous_health
    if "images" in what:
        payload["images"] = row.images
        payload["running_images"] = row.running_images
    return _Change(
        guid=str(row.guid),
        payload=payload,
        organization_id=organization_id,
        team_id=team_id,
        project_id=project_id,
    )


def _owner(row: ProductDeployment, cluster: TenantCluster) -> tuple[int | None, int | None, int | None]:
    """Organization, team and project an event about ``row`` belongs to.

    The cluster's organization; on a shared cluster, the assigned project's.
    A shared cluster is visible to every organization, so an unassigned row
    on one belongs to none of them.
    """
    project = row.project if row.project_id else None
    if project is not None and project.deleted_at is not None:
        project = None
    if cluster.organization_id is not None:
        if project is not None and project.organization_id == cluster.organization_id:
            return cluster.organization_id, project.team_id, project.id
        return cluster.organization_id, None, None
    if project is not None:
        return project.organization_id, project.team_id, project.id
    return None, None, None


def _severity(health: str, what: list[str]) -> str:
    if "gone" in what:
        return "info"
    if health == ProductDeployment.Health.DOWN:
        return "error"
    if health == ProductDeployment.Health.DEGRADED:
        return "warn"
    return "info"


def _emit(change: _Change) -> None:
    if change.organization_id is None:
        # The event log is per organization and none owns an unassigned
        # product on a shared cluster, so there is no one to tell.
        log.info(
            "product scan: %s %s on a shared cluster, unassigned: %s",
            change.payload["kind"],
            change.payload["namespace"],
            ", ".join(change.payload["changes"]),
        )
        return
    # Event.emit fills a missing team or project from the ambient tenant; a
    # caller's tenant must not claim an event that is not its own.
    owner = TenantContext(
        organization_id=change.organization_id,
        team_id=change.team_id,
        project_id=change.project_id,
    )
    with tenant_context(owner):
        Event.emit(
            PRODUCT_DEPLOYMENT_CHANGED,
            payload=change.payload,
            resource_kind="product_deployment",
            resource_id=change.guid,
            organization_id=change.organization_id,
            team_id=change.team_id,
            project_id=change.project_id,
        )


def _bounded(text: str) -> str:
    return text[:_MAX_ERROR_LENGTH]
