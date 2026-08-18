"""An in-cluster variant, booked from a cloud-hosted cluster (#1484).

``_sdk.coverage.is_portable`` counts an in-cluster variant as parity on every
cloud, and the lifecycle activities could not honour that: driver lookup was
scoped to the cluster's own plugin, and no cloud plugin registers a
``k8s_native`` driver. A ``cache/memcached`` row on an EKS cluster died at
resolution, so the four kinds that are portable only by way of an in-cluster
variant were unbookable everywhere the coverage doc said they were covered.

These run the activity path against the real plugin registry -- the same
manifests the control plane loads at boot -- because the bug was in what the
registry could answer, and a fake registry would have answered whatever the
test wanted. Only the driver *instance* is substituted, after the real
resolution has happened, so nothing about the choice is faked.

The config assertion in each is the one a partial fix fails: the config has to
be built for the plugin the *driver* came from. Resolving a ``MemcachedDriver``
and then handing it ``managed_config_for("aws", ...)`` swaps a lookup error for
a constructor error, which is worse than the honest failure it replaced.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from k8s_native.managed.cache_memcached import MemcachedDriver

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_drivers.managed_resolution import (
    ResolvedManagedDriver,
    resolve_managed_driver,
)
from astrolift_identity.models import Organization, Project, Team
from astrolift_lifecycle.models import AppEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_services.models import ManagedService
from astrolift_workflows.activities.managed_service_lifecycle import (
    _deprovision_sync,
    _provision_sync,
)

pytestmark = pytest.mark.django_db

PROBE_PAYLOAD = {"kubernetes_version": "1.30.0", "installed_crds": []}
"""Memcached requires no operator CRDs, so a bare inventory clears preflight."""


def _make_service(*, kind: str, variant: str, plugin_slug: str, backend_ref: str = "") -> ManagedService:
    suffix = f"{plugin_slug}-{variant}"[:24]
    org = Organization.objects.create(name="Acme", slug=f"acme-{suffix}")
    team = Team.objects.create(organization=org, name="Eng", slug=f"eng-{suffix}")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug=f"demo-{suffix}")
    # bulk_create bypasses BaseCoreModel.save(), whose integer optimistic-version
    # bump collides with ProviderPlugin's CharField ``version``.
    ProviderPlugin.objects.bulk_create(
        [
            ProviderPlugin(
                name=plugin_slug.upper(),
                slug=plugin_slug,
                version="0.0.1",
                capabilities_manifest={},
                config_schema={},
            ),
        ],
        ignore_conflicts=True,
    )
    cluster = TenantCluster.objects.create(
        organization=org,
        slug=f"cluster-{suffix}",
        name=plugin_slug,
        provider_plugin=ProviderPlugin.objects.get(slug=plugin_slug),
        endpoint="https://example.invalid",
        region="us-east-1",
        provider_config={"cluster_name": "icv", "region": "us-east-1"},
    )
    app = RegisteredApp.objects.create(
        organization=org,
        team=team,
        project=project,
        name="ICV App",
        slug=f"app-{suffix}",
        provisioning_status="ready",
    )
    env = AppEnvironment.objects.create(registered_app=app, name="production", tenant_cluster=cluster)
    return ManagedService.objects.create(
        registered_app=app,
        app_environment=env,
        kind=kind,
        variant=variant,
        name="svc",
        config={},
        backend_ref=backend_ref,
    )


class _ConfigSpy:
    """Stands in for ``managed_config_for`` and remembers which plugin it was
    asked to build for -- the value a partial fix gets wrong."""

    def __init__(self) -> None:
        self.plugin_slugs: list[str] = []

    def __call__(self, plugin_slug, _cluster, *, kind, variant=""):  # noqa: ANN001 - test stub
        self.plugin_slugs.append(plugin_slug)
        return SimpleNamespace(kind=kind, variant=variant)


class _InertDriver:
    """Reports success without touching a cluster. Substituted only after the
    real resolver has run, so it never stands in for the choice itself."""

    def __init__(self, *, config):  # noqa: ANN001 - test stub
        self.config = config

    def provision(self, spec):  # noqa: ANN001 - test stub
        return SimpleNamespace(ok=True, handle="svc/handle", ready=True, message="", errors=[])

    def deprovision(self, spec, **_kwargs):  # noqa: ANN001 - test stub
        return SimpleNamespace(ok=True, handle="", message="deleted", errors=[], retryable=False)


class _RealResolution:
    """Runs the real resolution, records it, and swaps in ``_InertDriver``."""

    def __init__(self) -> None:
        self.resolved: list[ResolvedManagedDriver] = []

    def __call__(self, **kwargs) -> ResolvedManagedDriver:
        real = resolve_managed_driver(**kwargs)
        self.resolved.append(real)
        return ResolvedManagedDriver(
            plugin_slug=real.plugin_slug,
            driver_cls=_InertDriver,
            role=real.role,
        )

    @property
    def only(self) -> ResolvedManagedDriver:
        assert len(self.resolved) == 1, f"expected one resolution, got {len(self.resolved)}"
        return self.resolved[0]


def test_an_aws_cluster_provisions_the_in_cluster_memcached_driver():
    """Acceptance 1. Before the fallback this raised ``RuntimeError: plugin
    'aws' has no managed-service driver for kind='cache' variant='memcached'``
    while the coverage doc called ``cache`` portable on all three clouds."""
    svc = _make_service(kind="cache", variant="memcached", plugin_slug="aws")
    resolution, config_for = _RealResolution(), _ConfigSpy()

    with (
        patch("core.cluster_management.probe_cluster_capabilities_dispatch", return_value=PROBE_PAYLOAD),
        patch("core.cluster_observability.managed_config_for", config_for),
        patch("astrolift_drivers.managed_resolution.resolve_managed_driver", resolution),
    ):
        result = _provision_sync(svc.pk)

    assert result["ok"] is True
    assert resolution.only.driver_cls is MemcachedDriver
    assert config_for.plugin_slugs == ["k8s_native"], (
        "the driver came out of k8s_native, so its config has to be built for k8s_native; "
        f"got {config_for.plugin_slugs}"
    )


def test_teardown_of_an_in_cluster_service_resolves_the_same_way():
    """Provision reaching a driver that teardown cannot is the worse half of
    the bug: a StatefulSet left running with the row marked gone."""
    svc = _make_service(kind="cache", variant="memcached", plugin_slug="gcp", backend_ref="cache/icv")
    resolution, config_for = _RealResolution(), _ConfigSpy()

    with (
        patch("core.cluster_observability.managed_config_for", config_for),
        patch("astrolift_drivers.managed_resolution.resolve_managed_driver", resolution),
    ):
        result = _deprovision_sync(svc.pk, False, False)

    assert result["ok"] is True
    assert resolution.only.driver_cls is MemcachedDriver
    assert config_for.plugin_slugs == ["k8s_native"]


def test_a_cloud_managed_service_is_untouched_by_the_fallback():
    """Widening reach must not move an existing service: an ElastiCache row on
    AWS still gets the AWS driver and an AWS config."""
    svc = _make_service(kind="cache", variant="elasticache_memcached", plugin_slug="aws")
    resolution, config_for = _RealResolution(), _ConfigSpy()

    with (
        patch("core.cluster_observability.managed_config_for", config_for),
        patch("astrolift_drivers.managed_resolution.resolve_managed_driver", resolution),
    ):
        result = _provision_sync(svc.pk)

    assert result["ok"] is True
    assert resolution.only.plugin_slug == "aws"
    assert config_for.plugin_slugs == ["aws"]


def test_preflight_still_gates_an_in_cluster_variant_on_a_cloud_cluster():
    """The operator/CRD gate is keyed on (kind, variant) and probes through
    whichever cluster driver the cluster has, so a borrowed in-cluster driver
    is covered by it too. A memcached row on EKS that skipped preflight would
    apply a StatefulSet to a cluster nobody checked -- here an ancient control
    plane refuses before any driver is built."""
    svc = _make_service(kind="cache", variant="memcached", plugin_slug="azure")
    resolution, config_for = _RealResolution(), _ConfigSpy()

    with (
        patch(
            "core.cluster_management.probe_cluster_capabilities_dispatch",
            return_value={"kubernetes_version": "1.10.0", "installed_crds": []},
        ),
        patch("core.cluster_observability.managed_config_for", config_for),
        patch("astrolift_drivers.managed_resolution.resolve_managed_driver", resolution),
        pytest.raises(Exception, match="Kubernetes"),
    ):
        _provision_sync(svc.pk)

    assert not resolution.resolved, "preflight must refuse before a driver is resolved"
