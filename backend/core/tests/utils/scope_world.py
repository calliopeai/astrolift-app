"""A two-team org for testing sub-org permission scopes (#1731).

The sweep threading ``scope=`` through the sub-org permission gates needs
the same fixture in every module: one org, two teams, a project and an
app under each, plus a user who holds a role at exactly one scope. The
assertions that matter are always the same four --

* a binding at the object's own scope reaches it,
* the same binding does not reach a sibling object,
* a binding in another org reaches nothing,
* an org-scoped binding still reaches everything.

so the world they run against belongs in one place rather than being
re-typed per module.
"""

from __future__ import annotations

from types import SimpleNamespace

from django.contrib.auth import get_user_model

from astrolift_identity.models import Organization, Project, Role, RoleBinding, Team
from astrolift_registry.models import RegisteredApp
from core.tenancy import TenantContext, tenant_context


class ScopeWorld:
    """One org with two teams, each with a project and an app.

    ``suffix`` keeps slugs unique across the modules that build one.
    """

    def __init__(self, suffix: str):
        self.org = Organization.objects.create(name="Acme", slug=f"acme-{suffix}")
        self.medops = Team.objects.create(organization=self.org, name="MedOps", slug=f"medops-{suffix}")
        self.platform = Team.objects.create(organization=self.org, name="Platform", slug=f"platform-{suffix}")
        self.medops_project = Project.objects.create(
            organization=self.org, team=self.medops, name="Intake", slug=f"intake-{suffix}"
        )
        self.platform_project = Project.objects.create(
            organization=self.org, team=self.platform, name="Core", slug=f"core-{suffix}"
        )
        self.medops_app = RegisteredApp.objects.create(
            organization=self.org,
            team=self.medops,
            project=self.medops_project,
            name="QsOps",
            slug=f"qs-ops-{suffix}",
            k8s_namespace=f"acme-qs-ops-{suffix}",
            provisioning_status="ready",
        )
        self.platform_app = RegisteredApp.objects.create(
            organization=self.org,
            team=self.platform,
            project=self.platform_project,
            name="Gateway",
            slug=f"gateway-{suffix}",
            k8s_namespace=f"acme-gateway-{suffix}",
            provisioning_status="ready",
        )


def make_cluster(world: ScopeWorld, suffix: str):
    """A minimal tenant cluster for the rows that require one.

    ``ProviderPlugin.save`` bumps ``version`` on every write, so the row
    goes in through ``bulk_create`` the way the rest of the suite does.
    """
    from astrolift_clusters.models import ProviderPlugin, TenantCluster

    plugin = ProviderPlugin(
        name="K8s",
        slug=f"k8s-{suffix}",
        plugin_version="0.0.1",
        capabilities_manifest={},
        config_schema={},
    )
    ProviderPlugin.objects.bulk_create([plugin])
    return TenantCluster.objects.create(
        organization=world.org,
        name="prod",
        slug=f"prod-{suffix}",
        provider_plugin=ProviderPlugin.objects.get(slug=f"k8s-{suffix}"),
        provider_config={},
        endpoint="https://k8s.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
        auth_config={},
    )


def make_user(suffix: str):
    User = get_user_model()
    return User.objects.create(username=f"reba-{suffix}", email=f"reba-{suffix}@acme.test")


def make_info(user):
    """The minimal ``Info`` shape a resolver reads off ``context``."""
    return SimpleNamespace(context=SimpleNamespace(user=user, request=SimpleNamespace(user=user)))


def bind_role(user, *, permissions, kind: str, scope_id: int, slug: str) -> RoleBinding:
    """Give ``user`` ``permissions`` at exactly one scope."""
    role = Role.objects.create(
        name=slug,
        slug=slug,
        scope_level=getattr(Role.ScopeLevel, kind),
        permissions=[p.value for p in permissions],
        is_system=False,
    )
    return RoleBinding.objects.create(user=user, role=role, scope_kind=kind, scope_id=scope_id)


def as_tenant(world: ScopeWorld, user):
    return tenant_context(TenantContext(organization_id=world.org.id, actor_user_id=user.id))
