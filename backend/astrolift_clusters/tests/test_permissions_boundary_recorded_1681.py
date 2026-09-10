"""A cluster records the IAM boundary its roles must carry (#1681).

#1679 added `IRSAConfig.permissions_boundary_arn`, read from the tenant
cluster's `provider_config["iam_permissions_boundary_arn"]`. Nothing ever
wrote that key -- it appeared in no registration path, no adopt path and
nowhere in the installer -- so on every install the value resolved to
`""`, the driver omitted `PermissionsBoundary`, and the fix was correct,
tested and inert.

In pull mode the installer runs under a boundary whose
`DenyRoleCreationWithoutThisBoundary` statement refuses any `CreateRole`
that does not attach the same boundary. Astrolift mints two roles per app
-- `astrolift-<org>-<app>` for IRSA and `astrolift-build-<org>-<app>` for
the kaniko build -- and both are denied until the cluster knows which
boundary to attach.

The second half is the one that would have made it inert *again*:
`provider_config` is assigned wholesale on every container start, and is
`{}` on any run that does not auto-discover.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command

from astrolift_clusters.models import ProviderPlugin, TenantCluster

pytestmark = pytest.mark.django_db

SLUG = "pb-cluster"
BOUNDARY = "arn:aws:iam::123456789012:policy/AstroliftInstallerBoundary"


@pytest.fixture
def aws_plugin(db):
    plugin, _ = ProviderPlugin.objects.get_or_create(
        slug="aws",
        defaults={"name": "aws", "capabilities_manifest": {}, "config_schema": {}},
    )
    return plugin


def _register(**kwargs):
    call_command("register_tenant_cluster", slug=SLUG, plugin_slug="aws", **kwargs)


def _cluster():
    return TenantCluster.all_objects.get(slug=SLUG)


def test_the_boundary_can_be_declared_on_the_command(aws_plugin):
    _register(iam_permissions_boundary_arn=BOUNDARY)

    assert _cluster().provider_config["iam_permissions_boundary_arn"] == BOUNDARY


def test_the_boundary_can_come_from_the_environment(monkeypatch, aws_plugin):
    """The declarative path an install actually uses."""

    monkeypatch.setenv("ASTROLIFT_CLUSTER_IAM_PERMISSIONS_BOUNDARY_ARN", BOUNDARY)
    _register()

    assert _cluster().provider_config["iam_permissions_boundary_arn"] == BOUNDARY


def test_the_boundary_reaches_the_identity_driver_config(aws_plugin):
    """The point of recording it. Asserted end to end rather than on the
    column, because a value the driver never reads is the bug this
    issue is about."""

    from core.app_deploy import _config_for_capability

    _register(iam_permissions_boundary_arn=BOUNDARY)
    config = _config_for_capability("aws", _cluster(), "identity")

    assert config.permissions_boundary_arn == BOUNDARY


def test_a_rerun_without_it_does_not_delete_it(aws_plugin):
    """`provider_config` is assigned wholesale on every container start.
    Without the merge, the next deploy makes the fix inert again."""

    _register(iam_permissions_boundary_arn=BOUNDARY)
    _register()  # what every container start does

    assert _cluster().provider_config["iam_permissions_boundary_arn"] == BOUNDARY


def test_a_rerun_does_not_delete_other_discovered_values(aws_plugin):
    """The same wholesale assignment would drop account id, OIDC provider
    arn and ECR registry -- taking managed services and workload identity
    with them."""

    _register()
    cluster = _cluster()
    cluster.provider_config = {
        "account_id": "123456789012",
        "oidc_provider_arn": "arn:aws:iam::123456789012:oidc-provider/oidc.eks",
        "ecr_registry": "123456789012.dkr.ecr.us-west-2.amazonaws.com",
    }
    cluster.save(update_fields=["provider_config"])

    _register()

    pc = _cluster().provider_config
    assert pc["account_id"] == "123456789012"
    assert pc["oidc_provider_arn"].endswith("oidc.eks")
    assert pc["ecr_registry"].startswith("123456789012.dkr.ecr")


def test_the_command_value_wins_over_the_stored_one(aws_plugin):
    _register(iam_permissions_boundary_arn=BOUNDARY)
    rotated = BOUNDARY.replace("Boundary", "Boundary2")

    _register(iam_permissions_boundary_arn=rotated)

    assert _cluster().provider_config["iam_permissions_boundary_arn"] == rotated


def test_no_boundary_anywhere_leaves_the_key_absent(aws_plugin):
    """An empty string would read as "a boundary was configured and it is
    blank"; absent is the truth, and what the driver tests for."""

    _register()

    assert "iam_permissions_boundary_arn" not in _cluster().provider_config
