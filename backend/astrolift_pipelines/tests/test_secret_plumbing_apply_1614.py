"""Pipeline secrets actually reach the cluster (#1614).

`_apply_k8s_secret` and `_delete_k8s_secret` were broken three ways at once
and nothing noticed, because the existing suite covers only the pure helpers
in this module -- the redactor and `make_env_from_refs`. The two functions
that talk to a cluster had no test at all, which is exactly why an import of
a name that does not exist survived in both.

1. `core.cluster_observability` has no `get_dynamic_client`, so both raised
   ImportError on their first line.
2. Behind that, `client.resources.get(...)` is the raw `kubernetes.dynamic`
   API. The wrapper this repo builds (`providers/_sdk/k8s_dynamic_client.py`)
   has no `.resources` attribute, so a working accessor would not have helped.
3. The callers in `pipeline_job_spawn` passed `cluster=client` -- a K8s
   client into a parameter named `cluster`. Name and contents disagreed, and
   no type checker sees it because both sides are untyped `Any`.

So these assert against the driver contract the rest of the tree uses.
"""

from __future__ import annotations

import pytest

from astrolift_pipelines import secret_plumbing
from astrolift_pipelines.secret_plumbing import SecretResolutionError

MANIFEST = {
    "apiVersion": "v1",
    "kind": "Secret",
    "metadata": {"name": "pipeline-job-abc-secrets"},
    "stringData": {"TOKEN": "s3cr3t"},
}


class _Result:
    def __init__(self, ok=True, errors=None):
        self.ok = ok
        self.errors = errors or []


class _Driver:
    def __init__(self, result=None):
        self.result = result or _Result()
        self.applied: list = []
        self.deleted: list = []

    def apply_manifests(self, cluster_slug, namespace, manifests):
        self.applied.append((cluster_slug, namespace, manifests))
        return self.result

    def delete_manifests(self, cluster_slug, namespace, manifests):
        self.deleted.append((cluster_slug, namespace, manifests))
        return self.result


class _Cluster:
    slug = "tenant-a"


@pytest.fixture
def driver(monkeypatch):
    d = _Driver()
    import core.cluster_management as cm

    monkeypatch.setattr(cm, "_driver_for_cluster", lambda cluster: d)
    monkeypatch.setattr(cm, "_context_for_cluster", lambda cluster: type("C", (), {"slug": "tenant-a"})())
    return d


def test_apply_goes_through_the_cluster_driver(driver):
    secret_plumbing._apply_k8s_secret(_Cluster(), MANIFEST, "pipelines")

    assert len(driver.applied) == 1
    cluster_slug, namespace, manifests = driver.applied[0]
    assert namespace == "pipelines"
    assert manifests == [MANIFEST]


def test_delete_goes_through_the_cluster_driver(driver):
    secret_plumbing._delete_k8s_secret(_Cluster(), "pipeline-job-abc-secrets", "pipelines")

    assert len(driver.deleted) == 1
    _, namespace, manifests = driver.deleted[0]
    assert namespace == "pipelines"
    assert manifests[0]["metadata"]["name"] == "pipeline-job-abc-secrets"
    assert manifests[0]["kind"] == "Secret"


def test_a_failed_apply_raises_rather_than_reporting_success(monkeypatch):
    """`apply_manifests` reports failure in its return value, not by raising.
    Ignoring `result.ok` would leave the job running without its secrets and
    failing inside the container on an unresolved reference."""
    import core.cluster_management as cm

    failing = _Driver(_Result(ok=False, errors=["forbidden"]))
    monkeypatch.setattr(cm, "_driver_for_cluster", lambda cluster: failing)
    monkeypatch.setattr(cm, "_context_for_cluster", lambda cluster: type("C", (), {"slug": "s"})())

    with pytest.raises(SecretResolutionError) as exc:
        secret_plumbing._apply_k8s_secret(_Cluster(), MANIFEST, "pipelines")

    assert "forbidden" in str(exc.value)


def test_a_delete_that_finds_nothing_is_not_an_error(driver):
    """Cleanup runs on a path that may have partially failed, so a missing
    secret is the outcome it wants. The driver contract already treats
    not-found as ok; this holds that we do not second-guess it."""
    secret_plumbing._delete_k8s_secret(_Cluster(), "never-created", "pipelines")

    assert driver.deleted


def test_the_helpers_take_a_cluster_not_a_client():
    """The type confusion, pinned.

    Both functions call `_driver_for_cluster(cluster)`. Handing them a K8s
    dynamic client -- which is what `pipeline_job_spawn` did -- cannot work,
    and nothing else in the tree would have said so.

    Over the AST rather than the source text: the docstrings above
    deliberately name the dead symbols, so a substring check fails on the
    explanation of the bug rather than on the bug.
    """
    import ast
    import inspect

    for fn in (secret_plumbing._apply_k8s_secret, secret_plumbing._delete_k8s_secret):
        tree = ast.parse(inspect.getsource(fn).lstrip())
        imported = {
            alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for alias in node.names
        }
        attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}

        assert "_driver_for_cluster" in imported
        assert "get_dynamic_client" not in imported
        assert "resources" not in attributes
