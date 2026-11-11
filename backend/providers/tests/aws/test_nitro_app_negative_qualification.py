import importlib.util
import json
import sys
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("app_negative", SCRIPTS / "qualify_nitro_app_negative.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
sys.path.remove(str(SCRIPTS))


@pytest.fixture
def run():
    from aws.nitro_confidential import POLICY, key_policy, prepared_pod_hash

    guid = "11111111-1111-1111-1111-111111111111"
    compact = guid.replace("-", "")
    timestamp = datetime.now(UTC)
    config = SimpleNamespace(
        registry="123456789012.dkr.ecr.us-west-2.amazonaws.com/runs",
        service_account="runner",
        key_admin_arn="arn:aws:iam::123456789012:role/admin",
        runtime_role_arn="arn:aws:iam::123456789012:role/runtime",
        validate=lambda: None,
    )
    image = config.registry + "@sha256:" + "d" * 64
    pod = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": "agent-task-" + compact,
            "namespace": probe.NAMESPACE,
            "uid": "positive-uid",
            "labels": {"astrolift.dev/task-id": guid},
            "annotations": {POLICY: "policy"},
        },
        "spec": {
            "runtimeClassName": "confidential-sandbox",
            "serviceAccountName": "runner",
            "nodeName": "bound",
            "securityContext": {
                "runAsUser": 42042,
                "runAsGroup": 42042,
                "runAsNonRoot": True,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "containers": [
                {
                    "name": "probe",
                    "image": image,
                    "env": [{"name": "ASTROLIFT_CONFIDENTIAL_TASK_ID", "value": compact}],
                    "securityContext": {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}},
                }
            ],
        },
        "status": {"phase": "Running"},
    }
    checkpoint = {
        "task_id": compact,
        "callback_hash": "callback-hash",
        "registry": config.registry,
        "image": image,
        "policy": "policy",
        "pod_sha256": prepared_pod_hash(pod),
        "pcr17": "a" * 96,
        "key_arn": "arn:aws:kms:us-west-2:123456789012:key/11111111-1111-1111-1111-111111111111",
    }
    primary = SimpleNamespace(
        image_ref=probe.BASE_IMAGE,
        command=["python3", "-c"],
        args=[probe.PROBE.read_text()],
        updated_at=timestamp,
        env={},
    )
    task = SimpleNamespace(
        guid=guid,
        status="running",
        created_at=timestamp,
        cancel_requested_at=None,
        organization=SimpleNamespace(slug="conflict"),
        callback_token_hash="callback-hash",
        dispatch_target={
            "backend": "k8s_confidential_pod",
            "cluster_guid": probe.CLUSTER_GUID,
            "namespace": probe.NAMESPACE,
            "nitro_confidential": checkpoint,
        },
        brief=SimpleNamespace(guid="brief", content_hash="brief-hash", manifest_snapshot={}),
        environment_spec=SimpleNamespace(updated_at=timestamp, env_vars={}, secret_refs=[]),
        agent_definition=SimpleNamespace(
            containers=SimpleNamespace(filter=lambda **kw: SimpleNamespace(get=lambda: primary))
        ),
        result={
            "findings": [
                {
                    "sealed_inputs_ok": True,
                    "uid": 42042,
                    "gid": 42042,
                    "brief_id": "brief",
                    "brief_hash": "brief-hash",
                    "qualification": "run",
                }
            ]
        },
    )
    kms = Mock(spec=["describe_key", "list_resource_tags", "get_key_policy"])
    kms.describe_key.return_value = {"KeyMetadata": {"Arn": checkpoint["key_arn"], "KeyState": "Enabled"}}
    kms.list_resource_tags.return_value = {"Tags": [{"TagKey": "astrolift-task-id", "TagValue": compact}]}
    kms.get_key_policy.return_value = {"Policy": json.dumps(key_policy(config, checkpoint["pcr17"]))}
    ecr = Mock(spec=["describe_images"])
    ecr.describe_images.return_value = {"imageDetails": [{"imageTags": [compact], "imageDigest": "sha256:" + "d" * 64}]}
    return SimpleNamespace(
        task=task,
        pod=pod,
        config=config,
        checkpoint=checkpoint,
        pin=probe.digest(checkpoint),
        primary=primary,
        kms=kms,
        ecr=ecr,
    )


def test_inspect_accepts_real_identity_without_writes(run):
    cp, grant = probe.inspect(run.task, run.pod, run.config, run.kms, run.ecr, "positive-uid", run.pin)
    assert cp == run.checkpoint and len(grant) == 64
    assert not hasattr(run.kms, "put_key_policy")


@pytest.mark.parametrize(
    "change", ["fs_group", "null_fs_group", "uid", "gid", "root", "seccomp", "capabilities", "escalation", "privileged"]
)
def test_measured_positive_still_requires_the_qualified_sandbox_security_context(run, change):
    from aws.nitro_confidential import prepared_pod_hash

    security = run.pod["spec"]["securityContext"]
    container = run.pod["spec"]["containers"][0]["securityContext"]
    if change == "fs_group":
        security["fsGroup"] = 42042
    elif change == "null_fs_group":
        security["fsGroup"] = None
    elif change == "uid":
        security["runAsUser"] = 0
    elif change == "gid":
        security["runAsGroup"] = 0
    elif change == "root":
        security["runAsNonRoot"] = False
    elif change == "seccomp":
        security["seccompProfile"] = {"type": "Unconfined"}
    elif change == "capabilities":
        container["capabilities"]["add"] = ["SYS_ADMIN"]
    elif change == "escalation":
        container["allowPrivilegeEscalation"] = True
    else:
        container["privileged"] = True
    run.checkpoint["pod_sha256"] = prepared_pod_hash(run.pod)
    with pytest.raises(ValueError, match="sandbox security context"):
        probe.inspect(run.task, run.pod, run.config, run.kms, run.ecr, "positive-uid", probe.digest(run.checkpoint))
    run.kms.describe_key.assert_not_called()
    run.ecr.describe_images.assert_not_called()


@pytest.mark.parametrize(
    "change",
    [
        "cluster",
        "namespace",
        "uid",
        "checkpoint",
        "image",
        "policy",
        "phase",
        "callback",
        "label",
        "cleanup",
        "guard",
        "command",
        "finding",
        "brief",
        "task_state",
    ],
)
def test_refuses_changed_positive_before_cloud_reads(run, change):
    from aws.nitro_confidential import POLICY

    if change == "cluster":
        run.task.dispatch_target["cluster_guid"] = "foreign"
    elif change == "namespace":
        run.pod["metadata"]["namespace"] = "other"
    elif change == "uid":
        run.pod["metadata"]["uid"] = "replacement"
    elif change == "checkpoint":
        run.checkpoint["pcr17"] = "b" * 96
    elif change == "image":
        run.pod["spec"]["containers"][0]["image"] = "other"
    elif change == "policy":
        run.pod["metadata"]["annotations"][POLICY] = "other"
    elif change == "phase":
        run.pod["status"]["phase"] = "Succeeded"
    elif change == "callback":
        run.task.callback_token_hash = "other"
    elif change == "label":
        run.pod["metadata"]["labels"]["astrolift.dev/task-id"] = "other"
    elif change == "cleanup":
        run.checkpoint["cleanup_complete"] = True
    elif change == "guard":
        run.task.environment_spec.env_vars["QUALIFICATION_PUBLIC_VARIANT"] = "positive"
    elif change == "command":
        run.primary.args = ["print('wrong')"]
    elif change == "finding":
        run.task.result = {}
    elif change == "brief":
        run.task.brief.content_hash = "wrong"
    else:
        run.task.status = "completed"
    with pytest.raises(ValueError):
        probe.inspect(run.task, run.pod, run.config, run.kms, run.ecr, "positive-uid", run.pin)
    run.kms.describe_key.assert_not_called()


@pytest.mark.parametrize("change", ["disabled", "owner", "grant", "digest", "retag", "account"])
def test_refuses_key_or_ciphertext_ownership_change(run, change):
    if change == "disabled":
        run.kms.describe_key.return_value["KeyMetadata"]["KeyState"] = "PendingDeletion"
    elif change == "owner":
        run.kms.list_resource_tags.return_value["Tags"][0]["TagValue"] = "other"
    elif change == "grant":
        run.kms.get_key_policy.return_value["Policy"] = json.dumps({"Statement": []})
    elif change == "digest":
        run.ecr.describe_images.return_value["imageDetails"][0]["imageDigest"] = "sha256:other"
    elif change == "retag":
        run.ecr.describe_images.return_value["imageDetails"][0]["imageTags"].append("another-owner")
    else:
        run.checkpoint["key_arn"] = run.checkpoint["key_arn"].replace("123456789012", "999999999999")
    with pytest.raises(ValueError):
        probe.verify_key(run.checkpoint, run.config, run.kms, run.ecr)


def test_candidate_preserves_capsule_and_guard_but_has_distinct_identity(run):
    original = deepcopy(run.pod)
    candidate = probe.candidate_for(run.pod, "2" * 32, 120)
    assert run.pod == original
    assert "nodeName" not in candidate["spec"]
    assert candidate["metadata"]["labels"]["astrolift.dev/task-id"] != str(run.task.guid)
    assert candidate["spec"]["containers"][0]["env"] == original["spec"]["containers"][0]["env"] + [
        {"name": "QUALIFICATION_PUBLIC_VARIANT", "value": "negative"}
    ]
    assert candidate["spec"]["containers"][0]["image"] == run.checkpoint["image"]
    assert candidate["spec"]["securityContext"] == original["spec"]["securityContext"]
    assert "fsGroup" not in candidate["spec"]["securityContext"]


def submit_fixture(run, monkeypatch):
    from aws.nitro_confidential import POLICY

    k8s, state, saved = Mock(), {}, []

    def generate(candidate, *args):
        candidate["metadata"]["annotations"][POLICY] = "negative-policy"
        return candidate, "b" * 96

    monkeypatch.setattr(probe, "regenerate_negative", generate)

    def create(**kwargs):
        actual = deepcopy(kwargs["body"])
        actual["metadata"].update(uid="negative-uid", resourceVersion="42")
        k8s.get.return_value = actual
        return actual

    k8s._resource_for.return_value.create.side_effect = create
    k8s._to_dict.side_effect = lambda value: value

    def execute():
        probe.submit(
            lambda: (run.task, run.pod),
            run.config,
            run.kms,
            run.ecr,
            k8s,
            {},
            "positive-uid",
            run.pin,
            state,
            lambda: saved.append(deepcopy(state)),
        )

    return k8s, state, saved, execute


def test_submit_never_changes_grant_and_cleanup_deletes_exact_uid(run, monkeypatch):
    k8s, state, saved, execute = submit_fixture(run, monkeypatch)
    execute()
    assert saved[0]["stage"] == "prepared" and saved[1]["uid"] == "negative-uid"
    assert state["original_grant_sha256"]
    assert state["predicted_pcr17"] != run.checkpoint["pcr17"]
    assert state["image"] == run.checkpoint["image"] and state["key_arn"] == run.checkpoint["key_arn"]
    assert not hasattr(run.kms, "put_key_policy")
    probe.cleanup(k8s, state)
    k8s.delete.assert_called_once_with(
        kind="Pod", namespace=probe.NAMESPACE, name=state["name"], uid="negative-uid", resource_version="42"
    )


def test_changed_grant_during_generation_prevents_create(run, monkeypatch):
    k8s, _state, saved, execute = submit_fixture(run, monkeypatch)
    original = run.kms.get_key_policy.return_value
    run.kms.get_key_policy.side_effect = [original, {"Policy": json.dumps({"Statement": []})}]
    with pytest.raises(ValueError):
        execute()
    k8s._resource_for.return_value.create.assert_not_called()
    assert not saved


@pytest.mark.parametrize("change", ["uid", "label", "image", "namespace", "missing_uid"])
def test_cleanup_refuses_replacements_or_unrecorded_uid(run, monkeypatch, change):
    k8s, state, _, execute = submit_fixture(run, monkeypatch)
    execute()
    pod = k8s.get.return_value
    if change == "uid":
        pod["metadata"]["uid"] = "replacement"
    elif change == "label":
        pod["metadata"]["labels"][probe.LABEL] = "other"
    elif change == "image":
        pod["spec"]["containers"][0]["image"] = "other"
    elif change == "namespace":
        pod["metadata"]["namespace"] = "other"
    else:
        state.pop("uid")
    with pytest.raises(ValueError):
        probe.cleanup(k8s, state)
    k8s.delete.assert_not_called()


@pytest.mark.parametrize("exit_code", [0, 70, 1])
def test_any_guest_execution_is_negative_failure(run, monkeypatch, exit_code):
    k8s, state, _, execute = submit_fixture(run, monkeypatch)
    execute()
    k8s.get.return_value["status"] = {
        "phase": "Failed",
        "containerStatuses": [{"state": {"terminated": {"exitCode": exit_code}}}],
    }
    assert probe.observe(k8s, state)["attestation"] == "FAILED_guest_started"


def test_pod_failure_alone_never_proves_denial(run, monkeypatch):
    k8s, state, _, execute = submit_fixture(run, monkeypatch)
    execute()
    k8s.get.return_value["status"] = {"phase": "Failed"}
    assert probe.observe(k8s, state)["attestation"] == "inconclusive_audit_required"


def test_cli_defaults_to_inspect_and_mutation_is_explicit():
    args = probe.parse_args(
        ["--task", "task", "--positive-uid", "uid", "--checkpoint-sha256", "hash", "--receipt", "/private/receipt"]
    )
    assert args.action == "inspect"


def test_probe_negative_guard_exits_before_callback_or_contract(monkeypatch):
    import runpy
    import subprocess

    import requests

    callback, contract = Mock(), Mock()
    monkeypatch.setattr(requests, "post", callback)
    monkeypatch.setattr(subprocess, "run", contract)
    monkeypatch.setenv("QUALIFICATION_PUBLIC_VARIANT", "negative")
    with pytest.raises(SystemExit) as stopped:
        runpy.run_path(str(probe.PROBE))
    assert stopped.value.code == 70
    callback.assert_not_called()
    contract.assert_not_called()


def test_observation_rechecks_original_grant_and_never_claims_denial(run, monkeypatch):
    k8s, state, _, execute = submit_fixture(run, monkeypatch)
    execute()
    result = probe.observe_controlled(
        lambda: (run.task, run.pod), run.config, run.kms, run.ecr, k8s, "positive-uid", run.pin, state
    )
    assert result["original_grant_unchanged"] and result["key_enabled"]
    assert result["attestation"] == "inconclusive_audit_required"
    run.kms.describe_key.return_value["KeyMetadata"]["KeyState"] = "PendingDeletion"
    with pytest.raises(ValueError):
        probe.observe_controlled(
            lambda: (run.task, run.pod), run.config, run.kms, run.ecr, k8s, "positive-uid", run.pin, state
        )


def test_no_duplicate_submit_or_unchanged_measurement(run, monkeypatch):
    k8s, state, _, execute = submit_fixture(run, monkeypatch)
    monkeypatch.setattr(probe, "regenerate_negative", lambda candidate, *args: (candidate, run.checkpoint["pcr17"]))
    with pytest.raises(ValueError, match="PCR17"):
        execute()
    k8s._resource_for.return_value.create.assert_not_called()
    state["stage"] = "prepared"
    with pytest.raises(ValueError, match="duplicate"):
        execute()
    k8s._resource_for.return_value.create.assert_not_called()


def test_cleanup_absent_is_idempotent_without_cloud_or_app_access(run, monkeypatch):
    k8s, state, _, execute = submit_fixture(run, monkeypatch)
    execute()
    k8s.get.return_value = None
    probe.cleanup(k8s, state)
    k8s.delete.assert_not_called()


def test_receipt_is_private_atomic_and_pin_bound(tmp_path):
    import hashlib
    import stat

    path = tmp_path / "receipt.json"
    probe.write_receipt(path, {"uid": "first"})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    pin = hashlib.sha256(path.read_bytes()).hexdigest()
    assert probe.read_receipt(path, pin)["uid"] == "first"
    probe.write_receipt(path, {"uid": "second"})
    with pytest.raises(ValueError, match="SHA256"):
        probe.read_receipt(path, pin)
    assert not list(tmp_path.glob(".nitro-negative-*"))


def test_receipt_refuses_symlink_and_public_directory(tmp_path):
    path = tmp_path / "receipt.json"
    target = tmp_path / "target.json"
    target.write_text("{}")
    path.symlink_to(target)
    with pytest.raises(OSError):
        probe.read_receipt(path, "unused")
    path.unlink()
    tmp_path.chmod(0o755)
    with pytest.raises(ValueError, match="private"):
        probe.write_receipt(path, {})


def test_admitted_identity_environment_stays_bound_to_checkpoint(run):
    from aws.nitro_confidential import prepared_pod_hash

    run.pod["spec"]["containers"][0]["env"].append({"name": "AWS_STS_REGIONAL_ENDPOINTS", "value": "regional"})
    run.checkpoint["pod_sha256"] = prepared_pod_hash(run.pod)
    probe.verify_positive(run.task, run.pod, run.config, "positive-uid", probe.digest(run.checkpoint))
    run.pod["spec"]["containers"][0]["env"][-1]["value"] = "wrong"
    with pytest.raises(ValueError):
        probe.verify_positive(run.task, run.pod, run.config, "positive-uid", probe.digest(run.checkpoint))


def test_submit_binds_uid_to_post_response_without_get_adoption(run, monkeypatch):
    k8s, state, saved, execute = submit_fixture(run, monkeypatch)
    execute()
    k8s.get.assert_not_called()
    assert state["uid"] == "negative-uid"
    k8s.get.return_value["metadata"]["uid"] = "identical-replacement"
    with pytest.raises(ValueError, match="UID"):
        probe.cleanup(k8s, state)
    k8s.delete.assert_not_called()
    assert saved[1]["uid"] == "negative-uid"


def test_create_conflict_never_reads_or_adopts_existing_pod(run, monkeypatch):
    k8s, state, _, execute = submit_fixture(run, monkeypatch)
    k8s._resource_for.return_value.create.side_effect = RuntimeError("create conflict")
    with pytest.raises(RuntimeError, match="conflict"):
        execute()
    assert state["stage"] == "prepared" and "uid" not in state
    k8s.get.assert_not_called()
    with pytest.raises(ValueError, match="recorded UID"):
        probe.cleanup(k8s, state)
    k8s.delete.assert_not_called()
