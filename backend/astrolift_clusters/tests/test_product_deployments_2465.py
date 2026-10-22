"""The product-deployment scan (#2465): the installer's labels in, rows and events out.

The Deployment Manager labels and annotates each Workbench and Zentinelle
namespace it creates (contract version 1, calliope-installer
``docs/contracts/astrolift-product-deployments.md``). The scan lists them on
every managed cluster, keeps one ``ProductDeployment`` row per namespace and
emits ``product_deployment.changed``. The fixtures below are the contract's
own example namespace and its Workbench twin, served by a fake cluster
driver; everything from the dispatch layer down to the rows and events is
real.
"""

from __future__ import annotations

import ast
import datetime as dt
import pathlib
import uuid

import pytest
import yaml
from _sdk.cluster import NamespaceState, RunningImage, WorkloadHealth
from django.utils import timezone

from astrolift_clusters import product_deployments as pd
from astrolift_clusters.models import ProductDeployment, ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Project, Team
from astrolift_operations.models import Event
from core import cluster_management
from core.cluster_management import ClusterManagementError
from core.tenancy import TenantContext
from core.tenancy import tenant_context as as_tenant

pytestmark = pytest.mark.django_db

BACKEND = pathlib.Path(__file__).resolve().parents[2]

# The contract's example namespace, verbatim.
ZENTINELLE = """
apiVersion: v1
kind: Namespace
metadata:
  name: zentinelle
  labels:
    calliope.ai/product-contract: "1"
    calliope.ai/product: zentinelle
    calliope.ai/deployment: acme
    calliope.ai/managed-by: deployment-manager
    zentinelle-install: acme
  annotations:
    calliope.ai/images: '{"backend":"ghcr.io/calliopeai/zentinelle-backend@sha256:8cc9...","frontend":"ghcr.io/calliopeai/zentinelle-frontend@sha256:e281..."}'
    calliope.ai/endpoints: '{"api":"https://zentinelle.example.com/api/zentinelle/v1","console":"https://zentinelle.example.com"}'
    calliope.ai/workloads: deployment/zentinelle-backend,deployment/zentinelle-worker,deployment/zentinelle-frontend,deployment/zentinelle-temporal
    calliope.ai/integrations: zentinelle-astrolift,zentinelle-workbench
"""

# What workbench-serving writes, per the contract's tables.
WORKBENCH = """
apiVersion: v1
kind: Namespace
metadata:
  name: junohub
  labels:
    calliope.ai/product-contract: "1"
    calliope.ai/product: workbench
    calliope.ai/deployment: acme
    calliope.ai/managed-by: deployment-manager
    workbench-install: acme
  annotations:
    calliope.ai/images: '{"hub":"ghcr.io/calliopeai/junohub:0.1.60"}'
    calliope.ai/endpoints: '{"hub":"https://hub.acme.example.com"}'
    calliope.ai/workloads: deployment/hub
"""

BACKEND_IMAGE = "ghcr.io/calliopeai/zentinelle-backend@sha256:8cc9..."
FRONTEND_IMAGE = "ghcr.io/calliopeai/zentinelle-frontend@sha256:e281..."
HUB_IMAGE = "ghcr.io/calliopeai/junohub:0.1.60"
HUB_DIGEST = "sha256:0b1d"


def _namespace(document: str, *, uid: str = "", phase: str = "Active", labels=None, annotations=None):
    """A listed namespace from a contract document; ``None`` drops a key."""
    meta = yaml.safe_load(document)["metadata"]
    merged_labels = {**meta.get("labels", {}), **(labels or {})}
    merged_annotations = {**meta.get("annotations", {}), **(annotations or {})}
    return NamespaceState(
        name=meta["name"],
        labels={k: v for k, v in merged_labels.items() if v is not None},
        annotations={k: v for k, v in merged_annotations.items() if v is not None},
        phase=phase,
        uid=uid or f"uid-{meta['name']}",
    )


def _listed(document: str, **kwargs) -> dict:
    ns = _namespace(document, **kwargs)
    return {
        "name": ns.name,
        "uid": ns.uid,
        "labels": ns.labels,
        "annotations": ns.annotations,
        "phase": ns.phase,
    }


def _workload(namespace: str, name: str, *, desired: int = 1, ready: int = 1, images=()) -> WorkloadHealth:
    return WorkloadHealth(
        namespace=namespace,
        name=name,
        desired_replicas=desired,
        ready_replicas=ready,
        restart_count_24h=0,
        last_image_deployed_at="",
        running_images=tuple(RunningImage(image=i, image_id=d) for i, d in images),
    )


def _zentinelle_workloads(**ready: int) -> list[WorkloadHealth]:
    """Zentinelle's four declared Deployments, each fully ready unless overridden."""
    images = {
        "zentinelle-backend": [(BACKEND_IMAGE, BACKEND_IMAGE)],
        "zentinelle-worker": [(BACKEND_IMAGE, BACKEND_IMAGE)],
        "zentinelle-frontend": [(FRONTEND_IMAGE, FRONTEND_IMAGE)],
        "zentinelle-temporal": [("temporalio/auto-setup:1.25", "docker.io/temporalio/auto-setup@sha256:7e")],
    }
    return [
        _workload("zentinelle", name, desired=2, ready=ready.get(name.replace("-", "_"), 2), images=imgs)
        for name, imgs in images.items()
    ]


def _hub(*, ready: int = 1, images=((HUB_IMAGE, f"ghcr.io/calliopeai/junohub@{HUB_DIGEST}"),)):
    return [
        _workload("junohub", "hub", ready=ready, images=images),
        # The chart's proxy is out of the request path and not declared.
        _workload("junohub", "proxy", desired=1, ready=0),
    ]


class FakeDriver:
    """A cluster driver answering the two reads the scan makes."""

    def __init__(self, namespaces=(), workloads=(), *, list_error=None, health_error=None):
        self.namespaces = list(namespaces)
        self.workloads = list(workloads)
        self.list_error = list_error
        self.health_error = health_error
        self.selectors: list[str] = []
        self.health_reads: list[list[str] | None] = []

    def list_namespaces(self, cluster, *, label_selector):
        self.selectors.append(label_selector)
        if self.list_error is not None:
            raise self.list_error
        return list(self.namespaces)

    def list_workload_health(self, cluster, *, namespaces=None):
        self.health_reads.append(namespaces)
        if self.health_error is not None:
            raise self.health_error
        return [w for w in self.workloads if w.namespace in (namespaces or [])]


@pytest.fixture
def drivers(monkeypatch):
    """Cluster slug to its fake driver, behind the real dispatch layer."""
    by_slug: dict[str, FakeDriver] = {}
    monkeypatch.setattr(cluster_management, "_driver_for_cluster", lambda cluster: by_slug[cluster.slug])
    return by_slug


@pytest.fixture
def plugin():
    [row] = ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name="local",
                slug=f"local-{uuid.uuid4().hex[:6]}",
                capabilities_manifest={},
                config_schema={},
            )
        ]
    )
    return row


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug=f"acme-{uuid.uuid4().hex[:6]}")


@pytest.fixture
def other_org():
    return Organization.objects.create(name="Globex", slug=f"globex-{uuid.uuid4().hex[:6]}")


def _cluster(plugin, *, organization=None, **fields) -> TenantCluster:
    values = {
        "lifecycle": TenantCluster.Lifecycle.MANAGED.value,
        "is_active": True,
        **fields,
    }
    return TenantCluster.objects.create(
        organization=organization,
        slug=f"c-{uuid.uuid4().hex[:6]}",
        name="cluster",
        provider_plugin=plugin,
        provider_config={},
        endpoint="https://invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={"kubeconfig": "fake"},
        **values,
    )


def _project(org, name: str = "platform") -> Project:
    team = Team.objects.create(organization=org, name="Eng", slug=f"eng-{uuid.uuid4().hex[:6]}")
    return Project.objects.create(
        organization=org, team=team, name=name, slug=f"{name}-{uuid.uuid4().hex[:6]}"
    )


@pytest.fixture
def scan(django_capture_on_commit_callbacks):
    """``scan_cluster`` with its after-commit events run."""

    def _scan(cluster, **kwargs):
        with django_capture_on_commit_callbacks(execute=True):
            return pd.scan_cluster(cluster, **kwargs)

    return _scan


def _events(**filters):
    return list(
        Event.objects.filter(event_type=pd.PRODUCT_DEPLOYMENT_CHANGED, **filters).order_by(
            "occurred_at", "id"
        )
    )


@pytest.fixture
def envelopes():
    """Every ``product_deployment.changed`` envelope emitted, persisted or not."""
    from core.events import register_event_subscriber, unregister_event_subscriber

    seen = []

    def capture(envelope):
        if envelope.event_type == pd.PRODUCT_DEPLOYMENT_CHANGED:
            seen.append(envelope)

    register_event_subscriber(capture)
    yield seen
    unregister_event_subscriber(capture)


# ---- reading the contract ------------------------------------------------------


def test_the_selector_is_the_contracts():
    assert pd.PRODUCT_SELECTOR == "calliope.ai/product-contract,calliope.ai/product in (workbench,zentinelle)"


def test_the_contract_example_reads_as_a_zentinelle_deployment():
    product = pd.read_product_namespace(_listed(ZENTINELLE, uid="9f2c"))

    assert product == pd.ProductNamespace(
        namespace="zentinelle",
        uid="9f2c",
        kind="zentinelle",
        deployment_name="acme",
        managed_by="deployment-manager",
        contract_version=1,
        contract_supported=True,
        images={"backend": BACKEND_IMAGE, "frontend": FRONTEND_IMAGE},
        endpoints={
            "api": "https://zentinelle.example.com/api/zentinelle/v1",
            "console": "https://zentinelle.example.com",
        },
        workloads=("zentinelle-backend", "zentinelle-worker", "zentinelle-frontend", "zentinelle-temporal"),
        integrations=("zentinelle-astrolift", "zentinelle-workbench"),
    )


def test_integrations_are_read_on_zentinelle_only():
    product = pd.read_product_namespace(
        _listed(WORKBENCH, annotations={"calliope.ai/integrations": "zentinelle-astrolift"})
    )

    assert product.kind == "workbench"
    assert product.integrations == ()
    assert product.problems == ()


def test_reserved_and_unknown_keys():
    """``calliope.ai/version`` is read once written; anything unknown is ignored."""
    product = pd.read_product_namespace(
        _listed(WORKBENCH, annotations={"calliope.ai/version": "0.1.60", "calliope.ai/someday": "{not json"})
    )

    assert product.product_version == "0.1.60"
    assert product.problems == ()


@pytest.mark.parametrize(
    ("change", "why"),
    [
        ({"labels": {"astrolift.io/control-plane-scope": "true"}}, "Astrolift's own namespace"),
        ({"phase": "Terminating"}, "a teardown in progress"),
        ({"labels": {"calliope.ai/product": "chatstudio"}}, "a product this Astrolift does not know"),
        ({"labels": {"calliope.ai/product-contract": None}}, "no contract label"),
    ],
)
def test_namespaces_that_are_not_listed_as_products(change, why):
    assert pd.read_product_namespace(_listed(ZENTINELLE, **change)) is None, why


def test_a_major_this_astrolift_does_not_read_lists_only_its_identity():
    product = pd.read_product_namespace(_listed(ZENTINELLE, labels={"calliope.ai/product-contract": "2"}))

    assert product == pd.ProductNamespace(
        namespace="zentinelle",
        uid="uid-zentinelle",
        kind="zentinelle",
        deployment_name="acme",
        managed_by="",
        contract_version=2,
        contract_supported=False,
    )


def test_an_unreadable_contract_version_is_recorded():
    product = pd.read_product_namespace(_listed(ZENTINELLE, labels={"calliope.ai/product-contract": "v1"}))

    assert (product.contract_version, product.contract_supported) == (0, False)
    assert product.problems == ("calliope.ai/product-contract 'v1' is not a version number",)


@pytest.mark.parametrize(
    ("annotation", "value", "field", "problem"),
    [
        ("calliope.ai/images", "{not json", "images", "calliope.ai/images is not JSON"),
        ("calliope.ai/images", '["a"]', "images", "calliope.ai/images is not an object of non-empty strings"),
        (
            "calliope.ai/images",
            '{"backend": {"ref": "x"}}',
            "images",
            "calliope.ai/images is not an object of non-empty strings",
        ),
        (
            "calliope.ai/endpoints",
            '{"console": "javascript:alert(1)"}',
            "endpoints",
            "calliope.ai/endpoints 'console' is not an absolute http(s) URL",
        ),
        (
            "calliope.ai/endpoints",
            '{"console": "/zentinelle"}',
            "endpoints",
            "calliope.ai/endpoints 'console' is not an absolute http(s) URL",
        ),
        (
            "calliope.ai/workloads",
            "deployment/zentinelle-backend,zentinelle-worker",
            "workloads",
            "calliope.ai/workloads entry 'zentinelle-worker' is not kind/name",
        ),
        (
            "calliope.ai/integrations",
            "zentinelle-astrolift,Zentinelle Workbench",
            "integrations",
            "calliope.ai/integrations is not a list of flags",
        ),
        (
            "calliope.ai/version",
            "x" * 129,
            "product_version",
            "calliope.ai/version is longer than 128 characters",
        ),
    ],
)
def test_a_malformed_annotation_empties_only_its_own_field(annotation, value, field, problem):
    good = pd.read_product_namespace(_listed(ZENTINELLE))
    product = pd.read_product_namespace(_listed(ZENTINELLE, annotations={annotation: value}))

    assert product.problems == (problem,)
    assert not getattr(product, field)
    for other in ("images", "endpoints", "workloads", "integrations", "deployment_name", "kind"):
        if other != field:
            assert getattr(product, other) == getattr(good, other), other


def test_workload_kinds_a_later_contract_adds_are_skipped():
    product = pd.read_product_namespace(
        _listed(
            WORKBENCH, annotations={"calliope.ai/workloads": "deployment/hub, statefulset/db ,deployment/hub"}
        )
    )

    assert product.workloads == ("hub",)
    assert product.problems == ()


@pytest.mark.parametrize(
    ("reference", "repository"),
    [
        ("ghcr.io/calliopeai/zentinelle-backend@sha256:8cc9", "ghcr.io/calliopeai/zentinelle-backend"),
        ("ghcr.io/calliopeai/junohub:0.1.60", "ghcr.io/calliopeai/junohub"),
        ("ghcr.io/calliopeai/junohub:0.1.60@sha256:aa", "ghcr.io/calliopeai/junohub"),
        ("jupyterhub/k8s-hub:4.0.0", "docker.io/jupyterhub/k8s-hub"),
        ("docker.io/jupyterhub/k8s-hub", "docker.io/jupyterhub/k8s-hub"),
        ("nginx:1.27", "docker.io/library/nginx"),
        ("docker.io/nginx", "docker.io/library/nginx"),
        ("index.docker.io/library/nginx:1.27", "docker.io/library/nginx"),
        ("registry.local:5000/team/app:v2", "registry.local:5000/team/app"),
        ("localhost/app", "localhost/app"),
    ],
)
def test_image_references_compare_by_repository(reference, repository):
    assert pd._repository(reference) == repository


@pytest.mark.parametrize(
    ("image_id", "digest"),
    [
        ("ghcr.io/calliopeai/junohub@sha256:0b1d", "sha256:0b1d"),
        ("docker-pullable://ghcr.io/calliopeai/junohub@sha256:0b1d", "sha256:0b1d"),
        ("sha256:0b1d", "sha256:0b1d"),
        ("", ""),
    ],
)
def test_image_ids_read_as_digests(image_id, digest):
    assert pd._digest(image_id) == digest


@pytest.mark.parametrize(
    ("image_id", "repository"),
    [
        ("ghcr.io/calliopeai/junohub@sha256:0b1d", "ghcr.io/calliopeai/junohub"),
        ("docker-pullable://ghcr.io/calliopeai/junohub@sha256:0b1d", "ghcr.io/calliopeai/junohub"),
        ("docker.io/library/busybox@sha256:73aa", "docker.io/library/busybox"),
        ("sha256:b7c873bd97bd", ""),
        ("", ""),
    ],
)
def test_image_ids_name_the_repository_they_were_pulled_from(image_id, repository):
    assert pd._image_id_repository(image_id) == repository


# ---- the dispatch layer ----------------------------------------------------------


def test_the_namespace_dispatch_raises_when_the_driver_does(plugin, drivers):
    cluster = _cluster(plugin)
    drivers[cluster.slug] = FakeDriver(list_error=TimeoutError("apiserver timed out"))

    with pytest.raises(ClusterManagementError, match="apiserver timed out"):
        cluster_management.cluster_namespaces_dispatch(cluster=cluster, label_selector=pd.PRODUCT_SELECTOR)


def test_the_namespace_dispatch_refuses_a_driver_without_the_read(plugin, monkeypatch):
    cluster = _cluster(plugin)
    monkeypatch.setattr(cluster_management, "_driver_for_cluster", lambda c: object())

    with pytest.raises(ClusterManagementError, match="does not implement list_namespaces"):
        cluster_management.cluster_namespaces_dispatch(cluster=cluster, label_selector=pd.PRODUCT_SELECTOR)


def test_the_health_dispatch_carries_the_running_images(plugin, drivers):
    cluster = _cluster(plugin)
    drivers[cluster.slug] = FakeDriver(workloads=_hub())

    rows = cluster_management.cluster_workload_health_dispatch(cluster=cluster, namespaces=["junohub"])

    assert rows[0]["running_images"] == [
        {"image": HUB_IMAGE, "image_id": f"ghcr.io/calliopeai/junohub@{HUB_DIGEST}"}
    ]
    assert rows[1]["running_images"] == []


# ---- the scan ----------------------------------------------------------------------


def test_the_scan_records_each_product_namespace(plugin, org, drivers, scan):
    cluster = _cluster(plugin, organization=org)
    driver = drivers[cluster.slug] = FakeDriver(
        [_namespace(ZENTINELLE), _namespace(WORKBENCH)], [*_zentinelle_workloads(), *_hub()]
    )
    now = timezone.now()

    outcome = scan(cluster, now=now)

    assert outcome == pd.ClusterScan(listed=True, changes=2)
    assert driver.selectors == [pd.PRODUCT_SELECTOR]
    assert driver.health_reads == [["junohub", "zentinelle"]]
    zentinelle = ProductDeployment.objects.get(cluster=cluster, namespace="zentinelle")
    assert (zentinelle.kind, zentinelle.deployment_name, zentinelle.managed_by) == (
        "zentinelle",
        "acme",
        "deployment-manager",
    )
    assert (zentinelle.contract_version, zentinelle.contract_supported) == (1, True)
    assert zentinelle.namespace_uid == "uid-zentinelle"
    assert zentinelle.health == ProductDeployment.Health.HEALTHY
    assert zentinelle.endpoints["console"] == "https://zentinelle.example.com"
    assert zentinelle.integrations == ["zentinelle-astrolift", "zentinelle-workbench"]
    # The backend image runs in the backend and the worker; the Temporal
    # image is no declared component.
    assert zentinelle.running_images == {"backend": ["sha256:8cc9..."], "frontend": ["sha256:e281..."]}
    assert zentinelle.workloads[0] == {
        "name": "zentinelle-backend",
        "found": True,
        "desired_replicas": 2,
        "ready_replicas": 2,
        "restart_count_24h": 0,
    }
    assert (zentinelle.first_seen_at, zentinelle.last_seen_at, zentinelle.gone_at) == (now, now, None)
    assert zentinelle.last_scan_error == ""
    hub = ProductDeployment.objects.get(cluster=cluster, namespace="junohub")
    # The undeclared proxy at zero ready does not count.
    assert hub.health == ProductDeployment.Health.HEALTHY
    assert hub.running_images == {"hub": [HUB_DIGEST]}
    assert hub.endpoints == {"hub": "https://hub.acme.example.com"}
    assert [e.payload["changes"] for e in _events()] == [["appeared"], ["appeared"]]


@pytest.mark.parametrize(
    "image_id_prefix",
    ["", "docker-pullable://"],
    ids=["containerd", "dockershim"],
)
def test_a_digest_pinned_image_is_matched_by_the_repository_its_image_id_names(
    plugin, org, drivers, scan, image_id_prefix
):
    """A container started from ``repo@sha256:...`` reports ``image`` as the
    node's local image id (``sha256:<config>``), not the reference: containerd
    2.3 on Kubernetes 1.37 does. Only ``imageID`` names the repository, and
    the contract's own Zentinelle images are pinned that way."""
    cluster = _cluster(plugin, organization=org)
    reported = {
        "zentinelle-backend": ("sha256:b7c873bd97bd", BACKEND_IMAGE),
        "zentinelle-worker": ("sha256:b7c873bd97bd", BACKEND_IMAGE),
        "zentinelle-frontend": ("sha256:0deac8b82696", FRONTEND_IMAGE),
        "zentinelle-temporal": ("sha256:5f2a08c1d3e4", "docker.io/temporalio/auto-setup@sha256:7e"),
    }
    drivers[cluster.slug] = FakeDriver(
        [_namespace(ZENTINELLE)],
        [
            _workload("zentinelle", name, desired=2, ready=2, images=[(image, f"{image_id_prefix}{image_id}")])
            for name, (image, image_id) in reported.items()
        ],
    )

    scan(cluster)

    row = ProductDeployment.objects.get(cluster=cluster)
    assert row.running_images == {"backend": ["sha256:8cc9..."], "frontend": ["sha256:e281..."]}


def test_a_preloaded_image_is_matched_by_the_reference_it_runs_as(plugin, org, drivers, scan):
    """An image loaded onto the node rather than pulled (an air-gapped node)
    has no repository digest: ``imageID`` is the bare local id, and only
    ``image`` names the repository."""
    cluster = _cluster(plugin, organization=org)
    drivers[cluster.slug] = FakeDriver([_namespace(WORKBENCH)], _hub(images=[(HUB_IMAGE, "sha256:b7c873bd97bd")]))

    scan(cluster)

    assert ProductDeployment.objects.get(cluster=cluster).running_images == {"hub": ["sha256:b7c873bd97bd"]}


def test_a_rescan_with_nothing_new_is_quiet(plugin, org, drivers, scan):
    cluster = _cluster(plugin, organization=org)
    drivers[cluster.slug] = FakeDriver([_namespace(WORKBENCH)], _hub())
    scan(cluster)
    later = timezone.now() + dt.timedelta(minutes=2)

    outcome = scan(cluster, now=later)

    assert outcome.changes == 0
    assert len(_events()) == 1
    assert ProductDeployment.objects.get(cluster=cluster).last_seen_at == later


@pytest.mark.parametrize(
    ("ready", "health"),
    [
        ({}, "healthy"),
        ({"zentinelle_worker": 1}, "degraded"),
        ({"zentinelle_worker": 1, "zentinelle_frontend": 0}, "down"),
        ({"zentinelle_temporal": 0}, "down"),
    ],
)
def test_health_follows_the_declared_workloads(plugin, org, drivers, scan, ready, health):
    cluster = _cluster(plugin, organization=org)
    drivers[cluster.slug] = FakeDriver([_namespace(ZENTINELLE)], _zentinelle_workloads(**ready))

    scan(cluster)

    assert ProductDeployment.objects.get(cluster=cluster).health == health


def test_a_declared_deployment_that_does_not_exist_is_down(plugin, org, drivers, scan):
    cluster = _cluster(plugin, organization=org)
    workloads = [w for w in _zentinelle_workloads() if w.name != "zentinelle-temporal"]
    drivers[cluster.slug] = FakeDriver([_namespace(ZENTINELLE)], workloads)

    scan(cluster)

    row = ProductDeployment.objects.get(cluster=cluster)
    assert row.health == ProductDeployment.Health.DOWN
    assert row.workloads[-1] == {
        "name": "zentinelle-temporal",
        "found": False,
        "desired_replicas": 0,
        "ready_replicas": 0,
        "restart_count_24h": 0,
    }


def test_health_is_unknown_when_no_workload_could_be_read(plugin, org, drivers, scan):
    cluster = _cluster(plugin, organization=org)
    drivers[cluster.slug] = FakeDriver([_namespace(ZENTINELLE), _namespace(WORKBENCH)], _hub())

    scan(cluster)

    row = ProductDeployment.objects.get(cluster=cluster, namespace="zentinelle")
    assert row.health == ProductDeployment.Health.UNKNOWN
    assert {w["found"] for w in row.workloads} == {None}
    assert row.running_images == {}
    assert row.last_scan_error == "no workload health could be read in the namespace"
    assert ProductDeployment.objects.get(cluster=cluster, namespace="junohub").health == "healthy"


def test_health_is_unknown_when_the_health_read_fails(plugin, org, drivers, scan):
    cluster = _cluster(plugin, organization=org)
    drivers[cluster.slug] = FakeDriver([_namespace(WORKBENCH)], health_error=RuntimeError("forbidden"))

    scan(cluster)

    row = ProductDeployment.objects.get(cluster=cluster)
    assert row.health == ProductDeployment.Health.UNKNOWN
    assert row.last_scan_error.startswith("could not read workload health: ")
    assert "forbidden" in row.last_scan_error


def test_a_namespace_without_declared_workloads_is_unknown_and_reads_nothing(plugin, org, drivers, scan):
    cluster = _cluster(plugin, organization=org)
    driver = drivers[cluster.slug] = FakeDriver(
        [_namespace(WORKBENCH, annotations={"calliope.ai/workloads": None})], _hub()
    )

    scan(cluster)

    assert ProductDeployment.objects.get(cluster=cluster).health == ProductDeployment.Health.UNKNOWN
    # Never an empty namespace list: the driver reads that as astrolift-system.
    assert driver.health_reads == []


def test_a_malformed_annotation_is_recorded_on_the_row_which_still_lists(plugin, org, drivers, scan):
    cluster = _cluster(plugin, organization=org)
    drivers[cluster.slug] = FakeDriver(
        [_namespace(WORKBENCH, annotations={"calliope.ai/endpoints": '{"hub": "hub.acme.example.com"}'})],
        _hub(),
    )

    scan(cluster)

    row = ProductDeployment.objects.get(cluster=cluster)
    assert row.endpoints == {}
    assert row.last_scan_error == "calliope.ai/endpoints 'hub' is not an absolute http(s) URL"
    assert row.health == ProductDeployment.Health.HEALTHY


def test_an_unsupported_major_lists_with_its_identity_only(plugin, org, drivers, scan):
    cluster = _cluster(plugin, organization=org)
    driver = drivers[cluster.slug] = FakeDriver(
        [_namespace(ZENTINELLE, labels={"calliope.ai/product-contract": "2"})], _zentinelle_workloads()
    )

    scan(cluster)

    row = ProductDeployment.objects.get(cluster=cluster)
    assert (row.kind, row.deployment_name, row.namespace) == ("zentinelle", "acme", "zentinelle")
    assert (row.contract_version, row.contract_supported) == (2, False)
    assert (row.images, row.endpoints, row.workloads, row.integrations) == ({}, {}, [], [])
    assert row.health == ProductDeployment.Health.UNKNOWN
    assert driver.health_reads == []


def test_a_namespace_the_list_no_longer_returns_is_gone(plugin, org, drivers, scan):
    cluster = _cluster(plugin, organization=org)
    driver = drivers[cluster.slug] = FakeDriver([_namespace(ZENTINELLE), _namespace(WORKBENCH)], _hub())
    scan(cluster)
    driver.namespaces = [_namespace(ZENTINELLE)]
    gone_at = timezone.now() + dt.timedelta(minutes=2)

    scan(cluster, now=gone_at)
    scan(cluster, now=gone_at + dt.timedelta(minutes=2))

    hub = ProductDeployment.objects.get(cluster=cluster, namespace="junohub")
    assert hub.gone_at == gone_at
    assert hub.deleted_at is None
    assert ProductDeployment.objects.get(cluster=cluster, namespace="zentinelle").gone_at is None
    gone = [e for e in _events() if e.payload["changes"] == ["gone"]]
    assert [e.payload["namespace"] for e in gone] == ["junohub"]


def test_a_terminating_namespace_is_gone(plugin, org, drivers, scan):
    cluster = _cluster(plugin, organization=org)
    driver = drivers[cluster.slug] = FakeDriver([_namespace(WORKBENCH)], _hub())
    scan(cluster)
    driver.namespaces = [_namespace(WORKBENCH, phase="Terminating")]
    driver.workloads = _hub(ready=0)

    scan(cluster)

    row = ProductDeployment.objects.get(cluster=cluster)
    assert row.gone_at is not None
    assert row.health == ProductDeployment.Health.HEALTHY
    assert [e.payload["changes"] for e in _events()] == [["appeared"], ["gone"]]


def test_a_failed_list_changes_nothing_but_the_error(plugin, org, drivers, scan):
    cluster = _cluster(plugin, organization=org)
    driver = drivers[cluster.slug] = FakeDriver([_namespace(WORKBENCH)], _hub())
    first = timezone.now()
    scan(cluster, now=first)
    driver.list_error = ConnectionError("apiserver unreachable")

    outcome = scan(cluster, now=first + dt.timedelta(minutes=2))

    assert outcome.listed is False
    row = ProductDeployment.objects.get(cluster=cluster)
    assert (row.gone_at, row.last_seen_at, row.health) == (None, first, ProductDeployment.Health.HEALTHY)
    assert row.last_scan_error.startswith("could not list product namespaces: ")
    assert "apiserver unreachable" in row.last_scan_error
    assert len(_events()) == 1


def test_a_clean_scan_clears_the_last_error(plugin, org, drivers, scan):
    cluster = _cluster(plugin, organization=org)
    driver = drivers[cluster.slug] = FakeDriver([_namespace(WORKBENCH)], _hub())
    scan(cluster)
    driver.list_error = ConnectionError("apiserver unreachable")
    scan(cluster)
    driver.list_error = None

    scan(cluster)

    assert ProductDeployment.objects.get(cluster=cluster).last_scan_error == ""


def test_health_and_image_changes_are_events(plugin, org, drivers, scan):
    cluster = _cluster(plugin, organization=org)
    driver = drivers[cluster.slug] = FakeDriver([_namespace(WORKBENCH)], _hub())
    scan(cluster)

    driver.workloads = _hub(ready=0)
    scan(cluster)
    new_image = "ghcr.io/calliopeai/junohub:0.1.61"
    driver.namespaces = [
        _namespace(WORKBENCH, annotations={"calliope.ai/images": f'{{"hub":"{new_image}"}}'})
    ]
    scan(cluster)
    driver.workloads = _hub(images=[(new_image, "ghcr.io/calliopeai/junohub@sha256:61")])
    scan(cluster)

    appeared, down, declared, rolled = _events()
    assert down.payload["changes"] == ["health"]
    assert (down.payload["health"], down.payload["previous_health"], down.severity) == (
        "down",
        "healthy",
        "error",
    )
    assert declared.payload["changes"] == ["images"]
    assert declared.payload["images"] == {"hub": new_image}
    # Declared but not rolled out: the hub's pods still run the old digest.
    assert declared.payload["running_images"] == {"hub": [HUB_DIGEST]}
    assert rolled.payload["changes"] == ["health", "images"]
    assert rolled.payload["running_images"] == {"hub": ["sha256:61"]}
    assert rolled.severity == "info"


def test_a_reinstall_keeps_its_row_and_project(plugin, org, drivers, scan):
    cluster = _cluster(plugin, organization=org)
    driver = drivers[cluster.slug] = FakeDriver([_namespace(WORKBENCH, uid="first")], _hub())
    scan(cluster)
    row = ProductDeployment.objects.get(cluster=cluster)
    project = _project(org)
    ProductDeployment.objects.filter(pk=row.pk).update(project=project)
    driver.namespaces = []
    scan(cluster)

    driver.namespaces = [_namespace(WORKBENCH, uid="second")]
    scan(cluster)

    again = ProductDeployment.objects.get(cluster=cluster)
    assert again.pk == row.pk
    assert (again.namespace_uid, again.gone_at, again.project_id) == ("second", None, project.pk)
    assert again.first_seen_at == row.first_seen_at
    assert [e.payload["changes"] for e in _events()] == [["appeared"], ["gone"], ["appeared"]]


def test_another_install_in_the_namespace_does_not_inherit_the_project(plugin, drivers, scan):
    """On a shared cluster the project link decides which organization sees
    the row, so a different install must start without it."""
    cluster = _cluster(plugin)
    driver = drivers[cluster.slug] = FakeDriver([_namespace(WORKBENCH)], _hub())
    scan(cluster)
    old = ProductDeployment.objects.get(cluster=cluster)
    old_org = Organization.objects.create(name="Old", slug=f"old-{uuid.uuid4().hex[:6]}")
    ProductDeployment.objects.filter(pk=old.pk).update(project=_project(old_org))

    driver.namespaces = [_namespace(WORKBENCH, labels={"calliope.ai/deployment": "globex"})]
    scan(cluster)

    retired = ProductDeployment.all_objects.get(pk=old.pk)
    assert retired.deleted_at is not None and retired.gone_at is not None
    current = ProductDeployment.objects.get(cluster=cluster)
    assert (current.deployment_name, current.project_id) == ("globex", None)
    # The old install's going is told to the organization that had it.
    [gone] = _events(organization=old_org)
    assert (gone.payload["changes"], gone.payload["deployment_name"]) == (["gone"], "acme")


# ---- which clusters, and the sweep ------------------------------------------------


def test_the_sweep_scans_managed_active_clusters_that_are_not_offline(
    plugin, org, drivers, django_capture_on_commit_callbacks
):
    now = timezone.now()
    scanned = {
        "never_seen": _cluster(plugin, organization=org),
        "connected": _cluster(plugin, organization=org, last_heartbeat_at=now - dt.timedelta(seconds=10)),
    }
    skipped = {
        "offline": _cluster(plugin, organization=org, last_heartbeat_at=now - dt.timedelta(hours=1)),
        "registered": _cluster(plugin, organization=org, lifecycle=TenantCluster.Lifecycle.REGISTERED.value),
        "inactive": _cluster(plugin, organization=org, is_active=False),
    }
    deleted = _cluster(plugin, organization=org)
    deleted.soft_delete()
    for cluster in [*scanned.values(), *skipped.values(), deleted]:
        drivers[cluster.slug] = FakeDriver([_namespace(WORKBENCH)], _hub())

    with django_capture_on_commit_callbacks(execute=True):
        assert pd.scan_product_deployments(now=now) == 2

    assert {c.slug for c in scanned.values()} == {
        slug for slug, driver in drivers.items() if driver.selectors
    }
    assert ProductDeployment.objects.filter(cluster__in=list(scanned.values())).count() == 2


def test_one_failing_cluster_does_not_stop_the_sweep(
    plugin, org, drivers, django_capture_on_commit_callbacks
):
    broken = _cluster(plugin, organization=org)
    fine = _cluster(plugin, organization=org)
    drivers[fine.slug] = FakeDriver([_namespace(WORKBENCH)], _hub())

    with django_capture_on_commit_callbacks(execute=True):
        # ``broken`` has no driver at all: the dispatch raises a KeyError.
        assert pd.scan_product_deployments() == 1

    assert ProductDeployment.objects.filter(cluster=fine).exists()
    assert not ProductDeployment.objects.filter(cluster=broken).exists()


# ---- who an event belongs to --------------------------------------------------------


def test_an_org_owned_clusters_events_go_to_its_organization(plugin, org, other_org, drivers, scan):
    cluster = _cluster(plugin, organization=org)
    drivers[cluster.slug] = FakeDriver([_namespace(WORKBENCH)], _hub())

    scan(cluster)

    [event] = _events()
    assert (event.organization_id, event.team_id, event.project_id) == (org.pk, None, None)
    row = ProductDeployment.objects.get(cluster=cluster)
    assert (event.resource_kind, event.resource_id) == ("product_deployment", str(row.guid))
    assert event.payload["cluster_slug"] == cluster.slug
    assert "endpoints" not in event.payload


def test_a_callers_team_does_not_claim_an_org_clusters_event(plugin, org, drivers, scan):
    """Event.emit fills a missing team or project from the ambient tenant,
    which inside a request is the caller's."""
    cluster = _cluster(plugin, organization=org)
    drivers[cluster.slug] = FakeDriver([_namespace(WORKBENCH)], _hub())
    project = _project(org)

    with as_tenant(TenantContext(organization_id=org.pk, team_id=project.team_id, project_id=project.pk)):
        scan(cluster)

    [event] = _events()
    assert (event.organization_id, event.team_id, event.project_id) == (org.pk, None, None)


def test_an_unassigned_product_on_a_shared_cluster_belongs_to_no_organization(
    plugin, org, other_org, drivers, scan, envelopes
):
    """Every organization sees a shared cluster, none of them owns what the
    installer runs on it, so nothing is emitted: not to a caller's tenant,
    as in a request, and not to the subscribers either."""
    cluster = _cluster(plugin)
    drivers[cluster.slug] = FakeDriver([_namespace(WORKBENCH)], _hub())

    with as_tenant(TenantContext(organization_id=other_org.pk, actor_user_id=None)):
        scan(cluster)

    assert ProductDeployment.objects.filter(cluster=cluster).exists()
    assert _events() == []
    assert envelopes == []


def test_an_assigned_product_on_a_shared_cluster_belongs_to_its_projects_organization(
    plugin, org, other_org, drivers, scan
):
    cluster = _cluster(plugin)
    driver = drivers[cluster.slug] = FakeDriver([_namespace(WORKBENCH)], _hub())
    scan(cluster)
    project = _project(org)
    ProductDeployment.objects.filter(cluster=cluster).update(project=project)
    driver.workloads = _hub(ready=0)

    with as_tenant(TenantContext(organization_id=other_org.pk)):
        scan(cluster)

    [event] = _events()
    assert (event.organization_id, event.team_id, event.project_id) == (org.pk, project.team_id, project.pk)
    assert _events(organization=other_org) == []


def test_a_soft_deleted_project_assigns_nothing(plugin, org, drivers, scan):
    cluster = _cluster(plugin)
    driver = drivers[cluster.slug] = FakeDriver([_namespace(WORKBENCH)], _hub())
    scan(cluster)
    project = _project(org)
    ProductDeployment.objects.filter(cluster=cluster).update(project=project)
    project.soft_delete()
    driver.workloads = _hub(ready=0)

    scan(cluster)

    assert _events() == []


def test_a_project_of_another_organization_does_not_move_an_org_clusters_event(
    plugin, org, other_org, drivers, scan
):
    cluster = _cluster(plugin, organization=org)
    driver = drivers[cluster.slug] = FakeDriver([_namespace(WORKBENCH)], _hub())
    scan(cluster)
    ProductDeployment.objects.filter(cluster=cluster).update(project=_project(other_org))
    driver.workloads = _hub(ready=0)

    scan(cluster)

    [appeared, down] = _events()
    assert (down.organization_id, down.team_id, down.project_id) == (org.pk, None, None)


# ---- Astrolift never writes the contract ---------------------------------------------


def test_only_the_reader_names_a_calliope_ai_key():
    """A namespace Astrolift creates never carries a ``calliope.ai/`` key, so
    nothing it creates can pass for a product (design section 2). The
    reader and its model are the only modules that name one."""
    allowed = {
        "astrolift_clusters/product_deployments.py",
        "astrolift_clusters/models/product_deployment.py",
    }
    named: set[str] = set()
    for path in BACKEND.rglob("*.py"):
        rel = path.relative_to(BACKEND)
        if {"tests", "testing", "migrations", ".venv", "node_modules"} & set(rel.parts):
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value.startswith("calliope.ai/")
            ):
                named.add(rel.as_posix())

    assert "astrolift_clusters/product_deployments.py" in named
    assert named <= allowed
