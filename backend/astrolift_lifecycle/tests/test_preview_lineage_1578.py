"""A preview knows what it is a preview of (#1578 feature 2).

The gap: a preview `AppEnvironment` had no link to a primary, so there was
no declared source for what a preview should inherit. That is why
`preview_managed_services.py` could not be wired -- not only because no
driver could carve a slice (feature 3, landed in PR #1649) but because
nothing said which instance to carve out *of*.

Scope is the relationship and nothing else. No test here reads or copies a
managed service: what flows across the link is feature 3's open
shared-vs-dedicated question. The point of landing the link separately is
that the two stop blocking each other.
"""

from __future__ import annotations

import pytest

from astrolift_lifecycle.models import AppEnvironment
from astrolift_lifecycle.services.preview_lineage import (
    is_preview_environment,
    resolve_previewed_environment,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def other_cluster(org, provider_plugin):
    """A second cluster, so the same-cluster filter can be tested at all."""
    from astrolift_clusters.models import TenantCluster

    return TenantCluster.objects.create(
        organization=org,
        name="other-cluster",
        slug="other-cluster",
        provider_plugin=provider_plugin,
        provider_config={},
        endpoint="https://other.cluster.invalid",
        auth_method=TenantCluster.AuthMethod.KUBECONFIG,
    )


def _env(app, cluster, name):
    return AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=cluster,
        name=name,
        required_approvals=0,
    )


def test_production_wins_over_an_older_environment(app, cluster):
    """Order of creation must not beat an explicit name. `dev` created
    first is the common case and previewing it would be wrong."""
    _env(app, cluster, "dev")
    prod = _env(app, cluster, "production")

    assert resolve_previewed_environment(app, cluster) == prod


def test_the_preference_order_is_honoured(app, cluster):
    _env(app, cluster, "staging")
    main = _env(app, cluster, "main")

    assert resolve_previewed_environment(app, cluster) == main


def test_the_oldest_is_the_tiebreak_when_no_name_matches(app, cluster):
    """Oldest, not newest, because it is stable: newest would move the
    lineage of every existing preview the moment someone adds an
    environment, and a preview silently changing what it is a preview of is
    worse than an arbitrary-but-fixed answer."""
    first = _env(app, cluster, "alpha")
    _env(app, cluster, "beta")

    assert resolve_previewed_environment(app, cluster) == first


def test_a_preview_is_never_chosen_as_a_primary(app, cluster):
    """Otherwise preview N previews preview N-1, and the lineage is a
    chain of ephemeral environments that all disappear together."""
    _env(app, cluster, "preview-pr-1")
    _env(app, cluster, "preview-feature-branch")

    assert resolve_previewed_environment(app, cluster) is None


def test_an_environment_on_another_cluster_is_not_eligible(app, cluster, other_cluster):
    """Hard filter, not a preference. Its managed services live in another
    cluster's namespaces, so inheriting from it would name resources the
    preview cannot reach -- a link that looks right and resolves to
    nothing."""
    _env(app, other_cluster, "production")

    assert resolve_previewed_environment(app, cluster) is None


def test_no_environment_at_all_resolves_to_none_rather_than_guessing(app, cluster):
    """A real state: the auto-PR path can create a preview before any
    normal environment exists. Left null, because a wrong lineage is worse
    than an absent one."""
    assert resolve_previewed_environment(app, cluster) is None


def test_a_soft_deleted_environment_is_not_eligible(app, cluster):
    """Repo law is soft delete, so a retired environment is still a row.
    Pointing at one would resolve to an environment whose namespaces are
    being torn down."""
    prod = _env(app, cluster, "production")
    prod.deleted_at = prod.created_at
    prod.save(update_fields=["deleted_at"])

    assert resolve_previewed_environment(app, cluster) is None


def test_deleting_the_primary_orphans_the_preview_rather_than_deleting_it(app, cluster):
    """`SET_NULL`, not `CASCADE`. Cascading would silently destroy live PR
    environments as a side effect of retiring an environment; an orphaned
    preview is recoverable."""
    prod = _env(app, cluster, "production")
    preview = _env(app, cluster, "preview-pr-7")
    preview.previewed_environment = prod
    preview.save(update_fields=["previewed_environment"])

    prod.delete()
    preview.refresh_from_db()

    assert preview.pk is not None, "the preview must survive"
    assert preview.previewed_environment_id is None


def test_the_reverse_accessor_lists_a_primarys_previews(app, cluster):
    prod = _env(app, cluster, "production")
    for n in (1, 2):
        env = _env(app, cluster, f"preview-pr-{n}")
        env.previewed_environment = prod
        env.save(update_fields=["previewed_environment"])

    assert prod.previews.count() == 2


@pytest.mark.parametrize(
    "name,expected",
    [
        ("preview-pr-42", True),
        ("preview-feature-x", True),
        ("production", False),
        ("prod-preview", False),
        ("", False),
    ],
)
def test_preview_detection_is_prefix_based(app, cluster, name, expected):
    """Prefix rather than a join to PreviewEnvironment, so the resolver
    stays usable from paths holding only an AppEnvironment. `prod-preview`
    is the case that makes substring matching wrong."""
    assert is_preview_environment(AppEnvironment(name=name)) is expected


def test_both_creation_paths_set_the_link():
    """The link is only worth having if the paths that create previews
    populate it. Asserted against the source rather than by driving two
    webhook/GraphQL flows, because what can regress here is somebody adding
    a third creation path or dropping the kwarg from one -- and that is a
    source-level fact.
    """
    import inspect

    import astrolift_lifecycle.schema.mutations.previews as mutation_path
    import astrolift_scm.webhook_views as webhook_path

    for module in (webhook_path, mutation_path):
        source = inspect.getsource(module)
        assert "previewed_environment=resolve_previewed_environment(" in source, (
            f"{module.__name__} creates a preview AppEnvironment without " "resolving what it is a preview of"
        )
