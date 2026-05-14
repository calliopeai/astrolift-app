"""Registry-test fixtures.

Most ``register_app`` tests build an Organization in-line and never
touch the cluster catalogue. The mutation rejects an org with zero
active TenantCluster rows (#315), so each test needs at least one
cluster against its org or the precondition trips before the case
under test runs. ``seed_cluster`` centralizes that helper.
"""

from __future__ import annotations

import pytest

from astrolift_clusters.models import ProviderPlugin, TenantCluster


@pytest.fixture
def seed_cluster():
    """Returns a callable that scaffolds an active TenantCluster
    against the given Organization.

    ``ProviderPlugin.version`` is a CharField but ``BaseCoreModel.save``
    increments a numeric ``version`` field, so we ``bulk_create`` the
    plugin row to dodge the conflict — same trick the rendered_manifest
    tests use. The cluster itself goes through the normal manager so
    it carries the audit columns the resolver query expects.
    """

    def _seed(org, *, slug: str = "default"):
        plugin = ProviderPlugin.objects.filter(slug=f"plugin-{slug}").first()
        if plugin is None:
            [plugin] = ProviderPlugin.objects.bulk_create(
                [
                    ProviderPlugin(
                        name=f"Plugin {slug}",
                        slug=f"plugin-{slug}",
                        capabilities_manifest={},
                        config_schema={},
                    )
                ]
            )
        return TenantCluster.objects.create(
            organization=org,
            name=f"cluster-{slug}",
            slug=f"cluster-{slug}",
            provider_plugin=plugin,
            provider_config={},
            endpoint="https://invalid",
            auth_method=TenantCluster.AuthMethod.KUBECONFIG,
            auth_config={},
            is_active=True,
        )

    return _seed
