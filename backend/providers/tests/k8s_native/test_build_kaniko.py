"""Tests for the in-cluster kaniko BuildDriver (#978)."""

from __future__ import annotations

from _sdk.build import BuildSpec
from _sdk.cluster import ApplyResult, DeleteResult, WorkloadStatus
from k8s_native.build_kaniko import (
    KanikoBuildDriver,
    _job_name,
    kaniko_context,
    render_build_service_account,
    render_kaniko_job,
)

# --------------------------------------------------------------------------
# Pure render helpers
# --------------------------------------------------------------------------


def test_kaniko_context_rewrites_scheme_and_keeps_ref():
    assert kaniko_context("git+https://github.com/acme/app#deadbeef") == "git://github.com/acme/app.git#deadbeef"


def test_kaniko_context_branch_ref_and_existing_dot_git():
    assert (
        kaniko_context("git+https://github.com/acme/app.git#refs/heads/main")
        == "git://github.com/acme/app.git#refs/heads/main"
    )


def test_kaniko_context_no_ref():
    assert kaniko_context("git+https://github.com/acme/app") == "git://github.com/acme/app.git"


def test_service_account_annotated_with_role_arn():
    sa = render_build_service_account(
        name="astrolift-build-acme-web", namespace="astrolift-system", role_arn="arn:aws:iam::1:role/r"
    )
    assert sa["kind"] == "ServiceAccount"
    assert sa["metadata"]["annotations"]["eks.amazonaws.com/role-arn"] == "arn:aws:iam::1:role/r"


def test_service_account_omits_annotation_when_no_role():
    sa = render_build_service_account(name="b", namespace="astrolift-system", role_arn="")
    assert "annotations" not in sa["metadata"]


def test_kaniko_job_renders_expected_args():
    job = render_kaniko_job(
        job_name="astrolift-build-x",
        namespace="astrolift-system",
        service_account="sa-x",
        context="git://github.com/acme/app.git#abc",
        dockerfile="Dockerfile",
        destination="123.dkr.ecr.us-west-2.amazonaws.com/acme/web:sha-abc",
        build_args={"VERSION": "1", "ARCH": "amd64"},
    )
    spec = job["spec"]
    args = spec["template"]["spec"]["containers"][0]["args"]
    assert "--context=git://github.com/acme/app.git#abc" in args
    assert "--dockerfile=Dockerfile" in args
    assert "--destination=123.dkr.ecr.us-west-2.amazonaws.com/acme/web:sha-abc" in args
    # push-retry absorbs the IRSA trust-propagation window on a first build.
    assert any(a.startswith("--push-retry=") for a in args)
    # build args are sorted + rendered as --build-arg=K=V
    assert args.index("--build-arg=ARCH=amd64") < args.index("--build-arg=VERSION=1")
    assert spec["template"]["spec"]["serviceAccountName"] == "sa-x"
    assert spec["backoffLimit"] >= 1
    assert spec["template"]["spec"]["restartPolicy"] == "Never"


def test_kaniko_job_context_sub_path_only_when_non_root():
    root = render_kaniko_job(
        job_name="j",
        namespace="ns",
        service_account="sa",
        context="c",
        dockerfile="Dockerfile",
        destination="r:t",
        context_sub_path=".",
    )
    sub = render_kaniko_job(
        job_name="j",
        namespace="ns",
        service_account="sa",
        context="c",
        dockerfile="Dockerfile",
        destination="r:t",
        context_sub_path="services/api",
    )
    root_args = root["spec"]["template"]["spec"]["containers"][0]["args"]
    sub_args = sub["spec"]["template"]["spec"]["containers"][0]["args"]
    assert not any(a.startswith("--context-sub-path") for a in root_args)
    assert "--context-sub-path=services/api" in sub_args


def test_job_name_is_dns_label_bounded():
    long_id = "9999-" + "a" * 200
    name = _job_name(long_id)
    assert len(name) <= 63
    assert name.startswith("astrolift-build-")
    # deterministic
    assert name == _job_name(long_id)


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
    return KanikoBuildDriver(
        cluster_driver=cluster,
        cluster_slug="tenant-1",
        service_account="astrolift-build-acme-web",
        service_account_role_arn="arn:aws:iam::1:role/astrolift-build-acme-web",
        poll_interval_seconds=0,
        sleep=lambda _s: None,
        **kw,
    )


_SPEC = BuildSpec(source_uri="git+https://github.com/acme/web#abc", dockerfile_path="Dockerfile")


def test_build_success_applies_sa_and_job_and_polls_complete():
    cluster = FakeClusterDriver(
        statuses=[
            _status([{"type": "Complete", "status": "False"}]),
            _status([{"type": "Complete", "status": "True"}]),
        ]
    )
    result = _driver(cluster).build(_SPEC, "123.dkr.ecr.us-west-2.amazonaws.com/acme/web", "sha-abc")

    assert result.success is True
    assert result.image_uri == "123.dkr.ecr.us-west-2.amazonaws.com/acme/web:sha-abc"
    # Applied exactly one bundle: [ServiceAccount, Job]
    assert len(cluster.applied) == 1
    kinds = [m["kind"] for m in cluster.applied[0][2]]
    assert kinds == ["ServiceAccount", "Job"]


def test_build_failure_surfaces_condition_message():
    cluster = FakeClusterDriver(
        statuses=[
            _status([{"type": "Failed", "status": "True", "message": "BackoffLimitExceeded"}]),
        ]
    )
    result = _driver(cluster).build(_SPEC, "repo", "t")
    assert result.success is False
    assert any("BackoffLimitExceeded" in e for e in result.errors)


def test_build_apply_failure_short_circuits_without_polling():
    bad_apply = ApplyResult(created=[], updated=[], unchanged=[], errors=["boom"])  # type: ignore[list-item]
    cluster = FakeClusterDriver(apply_result=bad_apply)
    result = _driver(cluster).build(_SPEC, "repo", "t")
    assert result.success is False
    assert result.errors


def test_build_times_out_when_never_terminal():
    # Clock jumps past the 5s timeout on the second read; conditions never
    # reach a terminal state.
    ticks = iter([0.0, 0.0, 10.0, 10.0, 10.0])
    cluster = FakeClusterDriver(statuses=[_status([]), _status([])])
    driver = _driver(cluster, timeout_seconds=5.0, clock=lambda: next(ticks))
    result = driver.build(_SPEC, "repo", "t")
    assert result.success is False
    assert any("timed out" in e for e in result.errors)


def test_cancel_deletes_job():
    cluster = FakeClusterDriver()
    _driver(cluster).cancel("42-abc")
    assert cluster.deleted
    _cluster, ns, manifests = cluster.deleted[0]
    assert ns == "astrolift-system"
    assert manifests[0]["kind"] == "Job"
    assert manifests[0]["metadata"]["name"] == _job_name("42-abc")
