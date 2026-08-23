"""An arch-label no-match explains itself (#1604).

`TenantCluster.node_arch` has no writer anywhere in the repo. The column
exists, `dispatch_router` reads it, and nothing has ever set it, so it holds
its `""` default on every install. The matcher treats an unknown
architecture as a mismatch:

    if required_arch and cluster.node_arch not in required_arch:
        return False

So **every arch-labelled job routes nowhere, on every install** -- and the
failure it produces sends the operator to look at their selector or their
cluster fleet, neither of which is the problem.

These do not change what matches. Treating "unknown" as a match would
schedule arm64 work onto amd64 nodes, which is a worse failure than an
honest refusal. What changes is that the refusal says what is actually
wrong.
"""

from __future__ import annotations

import pytest

from astrolift_pipelines.dispatch_router import ARCH_LABELS, _unrecorded_arch_hint

pytestmark = pytest.mark.django_db


class _Cluster:
    def __init__(self, slug, node_arch=""):
        self.slug = slug
        self.node_arch = node_arch


def test_the_hint_names_the_real_cause():
    clusters = [_Cluster("prod-a"), _Cluster("prod-b")]

    hint = _unrecorded_arch_hint(clusters, frozenset({"arm64"}))

    assert "no recorded node architecture" in hint
    assert "prod-a" in hint
    assert "#1604" in hint


def test_no_hint_when_the_selector_has_no_arch():
    """A selector that never mentioned an architecture did not fail for this
    reason, and saying so would be noise."""
    clusters = [_Cluster("prod-a")]

    assert _unrecorded_arch_hint(clusters, frozenset()) == ""


def test_no_hint_once_architectures_are_recorded():
    """The hint has to disappear on its own when #1604's producer lands, or
    it becomes a permanent lie."""
    clusters = [_Cluster("prod-a", node_arch="amd64"), _Cluster("prod-b", node_arch="arm64")]

    assert _unrecorded_arch_hint(clusters, frozenset({"arm64"})) == ""


def test_it_does_not_list_every_cluster():
    """A fleet-sized list in an error message is unreadable."""
    clusters = [_Cluster(f"c-{i}") for i in range(20)]

    hint = _unrecorded_arch_hint(clusters, frozenset({"amd64"}))

    assert "20 cluster(s)" in hint
    assert hint.count("c-") <= 4


def test_the_matcher_and_the_diagnostic_share_one_definition():
    """They used to disagree by construction: `arch_labels` was a local
    inside the matcher, so the diagnostic could not see it and a second copy
    was the only way to write this."""
    import ast
    import inspect

    from astrolift_pipelines import dispatch_router

    tree = ast.parse(inspect.getsource(dispatch_router._cluster_matches).lstrip())
    literals = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Set) and any(isinstance(e, ast.Constant) and e.value == "arm64" for e in n.elts)
    ]

    assert literals == [], "the matcher re-declares the arch label set instead of using ARCH_LABELS"
    assert "arm64" in ARCH_LABELS
