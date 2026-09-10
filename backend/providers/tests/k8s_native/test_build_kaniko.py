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


# --------------------------------------------------------------------------
# Failure diagnostics (#1686)
# --------------------------------------------------------------------------
#
# A failed Job reports "Job has reached the specified backoff limit" and
# nothing else, so every possible cause -- a repo it cannot clone, a
# Dockerfile step that exits non-zero, a push it cannot authenticate --
# read identically. On a private-endpoint cluster the operator cannot
# reach the pod to look either. The build pod's own output comes back
# with the failure.


class LoggingClusterDriver(FakeClusterDriver):
    def __init__(self, *, log_text="", raises=None, **kw):
        super().__init__(**kw)
        self._log_text = log_text
        self._raises = raises
        self.log_calls = []

    def read_job_pod_logs(self, cluster, namespace, job_name, *, tail_lines=100):
        self.log_calls.append((cluster, namespace, job_name, tail_lines))
        if self._raises is not None:
            raise self._raises
        return self._log_text


def test_a_failed_build_carries_the_pod_output():
    cluster = LoggingClusterDriver(
        statuses=[_status([{"type": "Failed", "status": "True", "message": "BackoffLimitExceeded"}])],
        log_text="error: failed to clone: authentication required",
    )
    result = _driver(cluster).build(_SPEC, "repo", "t")

    assert result.success is False
    # The condition still leads -- it is what the platform observed.
    assert "BackoffLimitExceeded" in result.errors[0]
    assert any("authentication required" in e for e in result.errors)
    # Read against the Job actually applied, in the build namespace.
    [(cluster_slug, namespace, job_name, _tail)] = cluster.log_calls
    assert cluster_slug == "tenant-1"
    assert namespace == "astrolift-system"
    assert job_name == cluster.applied[0][2][1]["metadata"]["name"]


def test_a_timed_out_build_carries_the_pod_output_too():
    """A build still running at the deadline is the case where the pod's
    output is the only thing that says what it is stuck on."""

    clock = iter([0.0, 0.0, 99.0, 99.0, 99.0])
    cluster = LoggingClusterDriver(
        statuses=[_status([]), _status([])],
        log_text="INFO: RUN npm ci",
    )
    result = _driver(cluster, timeout_seconds=5, clock=lambda: next(clock)).build(_SPEC, "repo", "t")

    assert result.success is False
    assert "timed out" in result.errors[0]
    assert any("npm ci" in e for e in result.errors)


def test_a_driver_that_cannot_read_logs_still_reports_the_failure():
    """The plain FakeClusterDriver has no ``read_job_pod_logs``."""

    cluster = FakeClusterDriver(
        statuses=[_status([{"type": "Failed", "status": "True", "message": "BackoffLimitExceeded"}])]
    )
    result = _driver(cluster).build(_SPEC, "repo", "t")

    assert result.success is False
    assert result.errors == ["BackoffLimitExceeded"]


def test_a_log_read_that_raises_does_not_replace_the_failure():
    """A diagnostic must never turn one failure into a different one."""

    cluster = LoggingClusterDriver(
        statuses=[_status([{"type": "Failed", "status": "True", "message": "BackoffLimitExceeded"}])],
        raises=RuntimeError("forbidden"),
    )
    result = _driver(cluster).build(_SPEC, "repo", "t")

    assert result.success is False
    assert "BackoffLimitExceeded" in result.errors[0]
    assert any("forbidden" in e for e in result.errors)


def test_empty_pod_output_adds_nothing():
    cluster = LoggingClusterDriver(
        statuses=[_status([{"type": "Failed", "status": "True", "message": "BackoffLimitExceeded"}])],
        log_text="   ",
    )
    result = _driver(cluster).build(_SPEC, "repo", "t")

    assert result.errors == ["BackoffLimitExceeded"]


def test_a_successful_build_reads_no_logs():
    cluster = LoggingClusterDriver(
        statuses=[_status([{"type": "Complete", "status": "True"}])],
        log_text="should not be read",
    )
    result = _driver(cluster).build(_SPEC, "repo", "t")

    assert result.success is True
    assert cluster.log_calls == []


# --------------------------------------------------------------------------
# Private-repo clone credential (#1685)
# --------------------------------------------------------------------------
#
# The build pod got a bare clone URL and no credential, so any private
# repo failed to clone and the pod died immediately. kaniko's git build
# context reads GIT_USERNAME / GIT_PASSWORD and turns them into HTTP
# basic auth, so the credential rides a Secret -- never the args, which
# are logged and readable off the Job.


def _job_of(cluster):
    [(_c, _ns, manifests)] = cluster.applied
    return next(m for m in manifests if m["kind"] == "Job")


def _secrets_of(cluster):
    [(_c, _ns, manifests)] = cluster.applied
    return [m for m in manifests if m["kind"] == "Secret"]


def test_a_credential_rides_a_secret_not_the_args():
    cluster = FakeClusterDriver(statuses=[_status([{"type": "Complete", "status": "True"}])])
    _driver(cluster, git_username="x-access-token", git_password="ghs_secret").build(_SPEC, "repo", "t")

    [secret] = _secrets_of(cluster)
    assert secret["stringData"] == {"GIT_USERNAME": "x-access-token", "GIT_PASSWORD": "ghs_secret"}

    job = _job_of(cluster)
    container = job["spec"]["template"]["spec"]["containers"][0]
    assert container["envFrom"] == [{"secretRef": {"name": secret["metadata"]["name"]}}]
    # The token appears nowhere a log line or a `kubectl get job` would show it.
    assert not any("ghs_secret" in a for a in container["args"])
    assert "ghs_secret" not in str(job)


def test_a_public_repo_gets_no_secret_and_no_envfrom():
    cluster = FakeClusterDriver(statuses=[_status([{"type": "Complete", "status": "True"}])])
    _driver(cluster).build(_SPEC, "repo", "t")

    assert _secrets_of(cluster) == []
    assert "envFrom" not in _job_of(cluster)["spec"]["template"]["spec"]["containers"][0]


def test_the_secret_is_deleted_after_the_build():
    cluster = FakeClusterDriver(statuses=[_status([{"type": "Complete", "status": "True"}])])
    _driver(cluster, git_password="ghs_secret").build(_SPEC, "repo", "t")

    [(_c, _ns, deleted)] = cluster.deleted
    assert deleted[0]["kind"] == "Secret"
    assert deleted[0]["metadata"]["name"] == _secrets_of(cluster)[0]["metadata"]["name"]


def test_the_secret_is_deleted_after_a_failed_build_too():
    """The Job self-deletes on its TTL; the Secret would otherwise
    outlive it, leaving a credential in the namespace."""

    cluster = FakeClusterDriver(
        statuses=[_status([{"type": "Failed", "status": "True", "message": "BackoffLimitExceeded"}])]
    )
    result = _driver(cluster, git_password="ghs_secret").build(_SPEC, "repo", "t")

    assert result.success is False
    assert cluster.deleted and cluster.deleted[0][2][0]["kind"] == "Secret"


def test_a_cleanup_failure_does_not_mask_the_build_outcome():
    class UndeletableCluster(FakeClusterDriver):
        def delete_manifests(self, cluster, namespace, manifests):
            raise RuntimeError("forbidden")

    cluster = UndeletableCluster(statuses=[_status([{"type": "Complete", "status": "True"}])])
    result = _driver(cluster, git_password="ghs_secret").build(_SPEC, "repo", "t")

    assert result.success is True


def test_the_username_defaults_to_the_github_documented_one():
    cluster = FakeClusterDriver(statuses=[_status([{"type": "Complete", "status": "True"}])])
    _driver(cluster, git_password="ghs_secret").build(_SPEC, "repo", "t")

    assert _secrets_of(cluster)[0]["stringData"]["GIT_USERNAME"] == "x-access-token"
