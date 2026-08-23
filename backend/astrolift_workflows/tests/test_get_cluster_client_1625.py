"""`_get_cluster_client` actually resolves a client (#1625).

It was wrong four ways and raised `TypeError` on its first statement, so no
pipeline job could spawn on any install. Nothing caught it because **every**
suite patches this function out -- `test_trigger_filtering` replaces it with
a thrower to scope a routing assertion, and `test_pipeline_dispatch_routing`
says in its own docstring that patching it is why bugs here survive.

That is the shape #1580 opens with: `_resolve_cluster` filtered on a field
that does not exist and raised `FieldError` on every call, surviving because
the path was well mocked. Same file, one function over.

So these drive the real function. They are the first tests that do.
"""

from __future__ import annotations

import pytest

from astrolift_workflows.activities import pipeline_job_spawn

pytestmark = pytest.mark.django_db


class _Client:
    """Stands in for KubernetesDynamicClient. Only identity matters here."""


class _Driver:
    def __init__(self):
        self.k8s_calls: list[str] = []

    def _k8s(self, cluster: str):
        self.k8s_calls.append(cluster)
        return _Client()


class _NoK8sDriver:
    """A cluster driver with no direct-apply client, e.g. a GitOps-only one."""


class _Cluster:
    slug = "tenant-a"
    auth_method = "kubeconfig"
    auth_config: dict = {}
    endpoint = ""
    ca_cert = ""
    ingress_class = "nginx"
    oidc_auth_config: dict = {}
    provider_plugin_id = 1
    provider_plugin = type("P", (), {"slug": "k8s_native"})()


@pytest.fixture
def wired(monkeypatch):
    """Resolve a cluster and a driver without a database or a plugin registry.

    Patching `_driver_for_cluster` rather than `_get_cluster_client` is the
    whole point: the function under test still runs.
    """
    driver = _Driver()
    monkeypatch.setattr(pipeline_job_spawn, "_resolve_cluster", lambda run, job=None: _Cluster())

    import core.cluster_management as cm

    monkeypatch.setattr(cm, "_driver_for_cluster", lambda cluster: driver)
    return driver


def test_it_returns_a_client_rather_than_raising(wired):
    """The regression. Before #1625 this raised TypeError on the first
    statement: `plugins.get` takes two arguments and got one."""
    client = pipeline_job_spawn._get_cluster_client(object())

    assert isinstance(client, _Client)


def test_the_client_is_built_for_the_resolved_cluster(wired):
    """`_k8s` is a method taking a cluster slug on all four drivers. Reading
    it as an attribute returned a bound method, and the caller's
    `client.server_side_apply(...)` then failed on it."""
    pipeline_job_spawn._get_cluster_client(object())

    assert wired.k8s_calls == ["tenant-a"]


def test_a_driver_without_a_k8s_client_fails_with_a_useful_message(monkeypatch):
    """GitOps-only drivers exist. The refusal must name the cluster and the
    plugin, because the operator's next question is which cluster."""
    monkeypatch.setattr(pipeline_job_spawn, "_resolve_cluster", lambda run, job=None: _Cluster())

    import core.cluster_management as cm

    monkeypatch.setattr(cm, "_driver_for_cluster", lambda cluster: _NoK8sDriver())

    with pytest.raises(RuntimeError) as exc:
        pipeline_job_spawn._get_cluster_client(object())

    assert "tenant-a" in str(exc.value)
    assert "k8s_native" in str(exc.value)


def test_it_goes_through_the_shared_driver_resolver():
    """Pinned structurally, because the four original defects were all
    consequences of hand-rolling this instead of calling the helper that
    already did it correctly -- including the credential guard, which the
    hand-rolled version skipped entirely.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(pipeline_job_spawn._get_cluster_client).lstrip())
    imported = {
        alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for alias in node.names
    }

    assert "_driver_for_cluster" in imported
    # The two hand-rolled steps that were wrong.
    assert "plugins" not in imported
    assert "_config_for" not in imported
