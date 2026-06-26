"""Tests for the in-cluster static-asset build/sync driver (#1010)."""

from __future__ import annotations

from _sdk.cluster import ApplyResult, DeleteResult, WorkloadStatus
from k8s_native.build_kaniko import _job_name
from k8s_native.build_static import (
    STATIC_BUILDER_IMAGE,
    StaticAssetBuildDriver,
    parse_git_source,
    render_static_build_job,
    render_static_build_service_account,
)

# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------


def test_parse_git_source_splits_url_and_ref():
    assert parse_git_source("git+https://github.com/acme/site#deadbeef") == (
        "https://github.com/acme/site",
        "deadbeef",
    )


def test_parse_git_source_branch_ref():
    assert parse_git_source("git+https://github.com/acme/site#refs/heads/main") == (
        "https://github.com/acme/site",
        "refs/heads/main",
    )


def test_parse_git_source_no_ref_and_trailing_slash():
    assert parse_git_source("git+https://github.com/acme/site/") == ("https://github.com/acme/site", "")


def test_service_account_annotated_with_role_arn():
    sa = render_static_build_service_account(
        name="astrolift-static-acme-site", namespace="astrolift-system", role_arn="arn:aws:iam::1:role/r"
    )
    assert sa["kind"] == "ServiceAccount"
    assert sa["metadata"]["annotations"]["eks.amazonaws.com/role-arn"] == "arn:aws:iam::1:role/r"
    # Distinct component label so the #995 scan + filters tell it apart.
    assert sa["metadata"]["labels"]["astrolift.io/component"] == "static-build"


def test_service_account_omits_annotation_when_no_role():
    sa = render_static_build_service_account(name="b", namespace="astrolift-system", role_arn="")
    assert "annotations" not in sa["metadata"]


def test_static_build_job_script_runs_clone_build_sync_invalidate():
    job = render_static_build_job(
        job_name="astrolift-static-x",
        namespace="astrolift-system",
        service_account="sa-x",
        source_context="git+https://github.com/acme/site#abc123",
        build_command="npm ci && npm run build",
        output_dir="dist",
        bucket="acme-site-assets",
        distribution_id="E123ABC",
        region="us-west-2",
    )
    spec = job["spec"]
    container = spec["template"]["spec"]["containers"][0]
    assert container["image"] == STATIC_BUILDER_IMAGE
    assert container["command"][0:2] == ["sh", "-c"]
    script = container["command"][2]
    # IRSA trust-propagation preflight before any real AWS call (#978 analog).
    assert "aws sts get-caller-identity" in script
    assert "git clone https://github.com/acme/site /workspace" in script
    assert "git checkout abc123" in script
    assert "npm ci && npm run build" in script
    assert "aws s3 sync dist s3://acme-site-assets --delete" in script
    assert "aws cloudfront create-invalidation --distribution-id E123ABC --paths '/*'" in script
    # Region threaded so the aws CLI targets the bucket's region.
    assert {"name": "AWS_REGION", "value": "us-west-2"} in container["env"]
    assert spec["template"]["spec"]["restartPolicy"] == "Never"
    assert spec["template"]["spec"]["serviceAccountName"] == "sa-x"
    assert spec["backoffLimit"] >= 1
    assert spec["ttlSecondsAfterFinished"] == 3600


def test_static_build_job_branch_ref_checkout_strips_refs_heads():
    job = render_static_build_job(
        job_name="j",
        namespace="ns",
        service_account="sa",
        source_context="git+https://github.com/acme/site#refs/heads/release",
        build_command="make",
        output_dir="public",
        bucket="b",
        distribution_id="D",
        region="eu-west-1",
    )
    script = job["spec"]["template"]["spec"]["containers"][0]["command"][2]
    assert "git checkout release" in script
    assert "refs/heads/release" not in script


def test_static_build_job_no_checkout_when_no_ref():
    job = render_static_build_job(
        job_name="j",
        namespace="ns",
        service_account="sa",
        source_context="git+https://github.com/acme/site",
        build_command="make",
        output_dir="public",
        bucket="b",
        distribution_id="D",
        region="eu-west-1",
    )
    script = job["spec"]["template"]["spec"]["containers"][0]["command"][2]
    assert "git checkout" not in script


# --------------------------------------------------------------------------
# Build orchestration — fakes for the ClusterDriver surface
# --------------------------------------------------------------------------


def _ok_apply():
    return ApplyResult(created=["Job/x"], updated=[], unchanged=[], errors=[])


def _status(conditions):
    return WorkloadStatus(
        kind="Job",
        name="x",
        namespace="astrolift-system",
        ready_replicas=0,
        desired_replicas=1,
        conditions=conditions,
    )


class FakeClusterDriver:
    def __init__(self, *, apply_result=None, statuses=None):
        self._apply_result = apply_result if apply_result is not None else _ok_apply()
        self._statuses = list(statuses or [])
        self.applied = []
        self.deleted = []

    def apply_manifests(self, cluster, namespace, manifests, *, dry_run=False):
        self.applied.append((cluster, namespace, manifests))
        return self._apply_result

    def get_workload_status(self, cluster, namespace, kind, name):
        if self._statuses:
            return self._statuses.pop(0)
        return _status([])

    def delete_manifests(self, cluster, namespace, manifests):
        self.deleted.append((cluster, namespace, manifests))
        return DeleteResult(deleted=["Job/x"], not_found=[], errors=[])


def _driver(cluster, **kw):
    return StaticAssetBuildDriver(
        cluster_driver=cluster,
        cluster_slug="tenant-1",
        service_account="astrolift-static-acme-site",
        service_account_role_arn="arn:aws:iam::1:role/astrolift-static-acme-site",
        poll_interval_seconds=0,
        sleep=lambda _s: None,
        **kw,
    )


def _build(driver):
    return driver.build(
        source_uri="git+https://github.com/acme/site#abc",
        build_command="npm run build",
        output_dir="dist",
        bucket="acme-site-assets",
        distribution_id="E123ABC",
        region="us-west-2",
    )


def test_build_success_applies_sa_and_job_and_polls_complete():
    cluster = FakeClusterDriver(
        statuses=[
            _status([{"type": "Complete", "status": "False"}]),
            _status([{"type": "Complete", "status": "True"}]),
        ]
    )
    result = _build(_driver(cluster))

    assert result.success is True
    assert result.image_uri == "s3://acme-site-assets"
    assert len(cluster.applied) == 1
    kinds = [m["kind"] for m in cluster.applied[0][2]]
    assert kinds == ["ServiceAccount", "Job"]


def test_build_failure_surfaces_condition_message():
    cluster = FakeClusterDriver(
        statuses=[_status([{"type": "Failed", "status": "True", "message": "BackoffLimitExceeded"}])]
    )
    result = _build(_driver(cluster))
    assert result.success is False
    assert any("BackoffLimitExceeded" in e for e in result.errors)


def test_build_apply_failure_short_circuits_without_polling():
    bad_apply = ApplyResult(created=[], updated=[], unchanged=[], errors=["boom"])  # type: ignore[list-item]
    cluster = FakeClusterDriver(apply_result=bad_apply)
    result = _build(_driver(cluster))
    assert result.success is False
    assert result.errors


def test_build_times_out_when_never_terminal():
    ticks = iter([0.0, 0.0, 10.0, 10.0, 10.0])
    cluster = FakeClusterDriver(statuses=[_status([]), _status([])])
    result = _build(_driver(cluster, timeout_seconds=5.0, clock=lambda: next(ticks)))
    assert result.success is False
    assert any("timed out" in e for e in result.errors)


def test_cancel_deletes_job():
    cluster = FakeClusterDriver()
    _driver(cluster).cancel("42-site-abc")
    assert cluster.deleted
    _cluster, ns, manifests = cluster.deleted[0]
    assert ns == "astrolift-system"
    assert manifests[0]["metadata"]["name"] == _job_name("42-site-abc")
