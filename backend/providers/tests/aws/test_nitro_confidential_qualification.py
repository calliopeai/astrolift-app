import importlib.util
import json
import stat
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "qualify_nitro_confidential.py"
spec = importlib.util.spec_from_file_location("confidential_live_probe", SCRIPT)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def test_observation_never_copies_runtime_messages():
    result = probe.pod_observation(
        {
            "metadata": {"name": "test", "uid": "uid"},
            "status": {
                "phase": "Failed",
                "message": "private-marker",
                "containerStatuses": [
                    {
                        "name": "agent",
                        "state": {"terminated": {"exitCode": 1, "message": "private-marker", "reason": "Error"}},
                    }
                ],
            },
        }
    )
    assert "private-marker" not in json.dumps(result)
    assert result["containers"][0]["terminated"]["exitCode"] == 1


def test_private_checkpoint_permissions_and_replacement(tmp_path):
    target = tmp_path / "state.json"
    probe.write_private(target, {"key_arns": ["arn:example"]})
    probe.write_private(target, {"key_arns": ["arn:replacement"]})
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert json.loads(target.read_text())["key_arns"] == ["arn:replacement"]
    assert not Path(str(target) + ".new").exists()


def test_cleanup_refuses_replacement_before_delete_or_key_mutation(monkeypatch):
    from aws.nitro_confidential import POLICY, prepared_pod_hash

    task_id = "a" * 32
    pod = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": "positive",
            "namespace": "qualification",
            "uid": "replacement",
            "labels": {"QualificationRun": "nitro-eks-test", "astrolift.dev/task-id": str(probe.uuid.UUID(task_id))},
            "annotations": {POLICY: "encoded"},
        },
        "spec": {"containers": [{"name": "agent", "image": "registry@sha256:123"}]},
    }
    saved = {
        "name": "positive",
        "uid": "original",
        "image": "registry@sha256:123",
        "prepared_pod_hash": prepared_pod_hash(pod),
        "policy_sha256": probe.hashlib.sha256(b"encoded").hexdigest(),
    }
    pods = Mock()
    pods.get.return_value.to_dict.return_value = deepcopy(pod)
    dynamic = SimpleNamespace(resources=SimpleNamespace(get=lambda **kw: pods))
    session = Mock()
    state = {"task_id": task_id, "run_id": "nitro-eks-test", "positive": saved}
    with pytest.raises(ValueError, match="replaced or mutated"):
        probe.cleanup({}, state, lambda: None, session, dynamic)
    pods.delete.assert_not_called()
    session.client.assert_not_called()


def test_source_hashes_match_exact_committed_helpers():
    import aws.nitro_confidential as helper

    for name, digest in probe.SOURCE_HASHES.items():
        assert probe.hashlib.sha256((Path(helper.__file__).parent / name).read_bytes()).hexdigest() == digest


def test_key_policy_normalizes_only_the_observed_action_scalar():
    expected = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "Run",
                "Effect": "Allow",
                "Principal": {"AWS": "arn:runtime"},
                "Action": ["kms:Decrypt"],
                "Resource": "*",
                "Condition": {"StringEqualsIgnoreCase": {"kms:RecipientAttestation:PCR17": "a" * 96}},
            }
        ],
    }
    actual = deepcopy(expected)
    actual["Statement"][0]["Action"] = "kms:Decrypt"
    assert probe.canonical_key_policy(actual) == probe.canonical_key_policy(expected)
    assert actual["Statement"][0]["Action"] == "kms:Decrypt"
    actual["Statement"][0]["Principal"]["AWS"] = ["arn:runtime"]
    assert probe.canonical_key_policy(actual) != probe.canonical_key_policy(expected)


@pytest.mark.parametrize(
    "variant", ["wildcard_action", "extra_action", "foreign_principal", "missing_condition", "extra_statement"]
)
def test_policy_normalization_cannot_accept_broader_grants(variant):
    expected = {
        "Statement": [
            {
                "Sid": "Run",
                "Effect": "Allow",
                "Principal": {"AWS": "arn:runtime"},
                "Action": ["kms:Decrypt"],
                "Resource": "*",
                "Condition": {"StringEqualsIgnoreCase": {"PCR": "pinned"}},
            }
        ]
    }
    actual = deepcopy(expected)
    if variant == "wildcard_action":
        actual["Statement"][0]["Action"] = "kms:*"
    elif variant == "extra_action":
        actual["Statement"][0]["Action"].append("kms:PutKeyPolicy")
    elif variant == "foreign_principal":
        actual["Statement"][0]["Principal"]["AWS"] = "*"
    elif variant == "missing_condition":
        actual["Statement"][0].pop("Condition")
    else:
        actual["Statement"].append(deepcopy(actual["Statement"][0]))
    assert probe.canonical_key_policy(actual) != probe.canonical_key_policy(expected)


def resume_fixture():
    from aws.nitro_confidential import POLICY, prepared_pod_hash

    task_id = "a" * 32
    image = "123456789012.dkr.ecr.us-west-2.amazonaws.com/runs@sha256:abc"
    pod = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": "positive",
            "namespace": "qualification",
            "uid": "original",
            "labels": {"QualificationRun": "nitro-eks-test", "astrolift.dev/task-id": str(probe.uuid.UUID(task_id))},
            "annotations": {POLICY: "encoded"},
        },
        "spec": {
            "nodeName": "scheduled-node",
            "schedulerName": "default-scheduler",
            "containers": [
                {"name": "agent", "image": image, "env": [{"name": "ASTROLIFT_CONFIDENTIAL_TASK_ID", "value": task_id}]}
            ],
        },
        "status": {
            "phase": "Succeeded",
            "containerStatuses": [{"name": "agent", "state": {"terminated": {"exitCode": 0}}}],
        },
    }
    saved = {
        "name": "positive",
        "uid": "original",
        "image": image,
        "prepared_pod_hash": prepared_pod_hash(pod),
        "policy_sha256": probe.hashlib.sha256(b"encoded").hexdigest(),
        "assertion_succeeded": True,
    }
    registry = image.split("@")[0]
    state = {
        "source_revision": probe.SOURCE_REVISION,
        "task_id": task_id,
        "run_id": "nitro-eks-test",
        "positive": saved,
        "checkpoint": {
            "task_id": task_id,
            "image": image,
            "pod_sha256": saved["prepared_pod_hash"],
            "policy": "encoded",
            "registry": registry,
            "pcr17": "a" * 96,
            "key_arn": "key",
        },
    }
    settings = {
        "run_id": "nitro-eks-test",
        "repository": registry,
        "key_admin_arn": "arn:aws:iam::123456789012:role/helper",
        "runtime_role_arn": "arn:aws:iam::123456789012:role/runtime",
        "region": "us-west-2",
    }
    return pod, state, settings


@pytest.mark.parametrize("variant", ["uid", "hash", "image", "policy", "not_succeeded", "prior_negative"])
def test_resume_refuses_changed_positive_before_any_mutation(monkeypatch, tmp_path, variant):
    pod, state, settings = resume_fixture()
    if variant == "uid":
        pod["metadata"]["uid"] = "replacement"
    elif variant == "hash":
        pod["spec"]["unexpected"] = True
    elif variant == "image":
        pod["spec"]["containers"][0]["image"] = "different"
    elif variant == "policy":
        pod["metadata"]["annotations"]["sandbox.amazonaws.com/policy"] = "different"
    elif variant == "not_succeeded":
        pod["status"]["phase"] = "Failed"
    else:
        state["negative"] = {"name": "already-submitted"}
    monkeypatch.setattr(probe, "preflight", lambda *args: None)
    pods = Mock()
    pods.get.return_value.to_dict.return_value = pod
    dynamic = SimpleNamespace(resources=SimpleNamespace(get=lambda **kwargs: pods))
    session = Mock()
    with pytest.raises(ValueError, match="resume"):
        probe.resume_negative(settings, tmp_path, state, lambda: None, session, dynamic, 60)
    pods.create.assert_not_called()
    session.client.assert_not_called()


def test_negative_generation_drops_only_scheduler_node_name(monkeypatch, tmp_path):
    from aws.nitro_confidential import POLICY, ConfidentialConfig, key_policy

    pod, state, settings = resume_fixture()
    config = ConfidentialConfig(
        registry=settings["repository"],
        key_admin_arn=settings["key_admin_arn"],
        runtime_role_arn=settings["runtime_role_arn"],
        runtime_directory=tmp_path,
        service_account="confidential-runner",
        temporary_root=str(tmp_path),
    )
    policy = key_policy(config, state["checkpoint"]["pcr17"])
    policy["Statement"][1]["Action"] = "kms:Decrypt"
    kms = SimpleNamespace(
        get_key_policy=lambda **kw: {"Policy": json.dumps(policy)},
        describe_key=lambda **kw: {"KeyMetadata": {"KeyState": "Enabled"}},
    )
    seen = []

    def regenerate(candidate, *args):
        seen.append(deepcopy(candidate))
        candidate["metadata"]["annotations"][POLICY] = "negative-policy"
        return candidate, "b" * 96

    monkeypatch.setattr(probe, "regenerate_negative", regenerate)
    monkeypatch.setattr(
        probe,
        "wait_pod",
        lambda *args: ({"metadata": {"name": "negative", "uid": "negative-uid"}, "status": {"phase": "Failed"}}, False),
    )
    pods = Mock()
    pods.create.return_value.to_dict.return_value = {"metadata": {"uid": "negative-uid"}}
    probe.execute_negative(pod, config, {}, None, kms, None, tmp_path, state, lambda: None, pods, 60)
    assert "nodeName" not in seen[0]["spec"]
    assert seen[0]["spec"]["schedulerName"] == "default-scheduler"
    assert state["negative"]["original_grant_unchanged"] is True
    assert state["negative"]["key_enabled_after"] is True
    assert not hasattr(kms, "put_key_policy")


def test_resume_preserves_original_positive_revision_and_never_creates_positive(monkeypatch, tmp_path):
    from aws.nitro_confidential import ConfidentialConfig

    pod, state, settings = resume_fixture()
    state["source_revision"] = "f30257f54"
    monkeypatch.setattr(probe, "preflight", lambda *args: None)
    # This local harness test does not require Linux's /dev/shm; no live path is changed.
    monkeypatch.setattr(ConfidentialConfig, "validate", lambda self: None)
    execute = Mock()
    monkeypatch.setattr(probe, "execute_negative", execute)
    pods = Mock()
    pods.get.return_value.to_dict.return_value = deepcopy(pod)
    dynamic = SimpleNamespace(resources=SimpleNamespace(get=lambda **kw: pods))
    session = Mock()
    session.get_credentials.return_value.get_frozen_credentials.return_value = SimpleNamespace(
        access_key="synthetic-access", secret_key="synthetic-secret", token=None
    )
    probe.resume_negative(settings, tmp_path, state, lambda: None, session, dynamic, 60)
    assert state["source_revision"] == "f30257f54"
    assert state["resume_helper_revision"] == probe.SOURCE_REVISION
    execute.assert_called_once()
    assert execute.call_args.args[0] == pod
    pods.create.assert_not_called()
    assert {item.args[0] for item in session.client.call_args_list} == {"kms", "ecr"}


@pytest.mark.parametrize("pin", [None, "bad", "0" * 64])
def test_missing_positive_requires_correct_explicit_snapshot_pin(monkeypatch, tmp_path, pin):
    pod, state, settings = resume_fixture()
    (tmp_path / "positive-observation-private.json").write_text(json.dumps(pod))
    monkeypatch.setattr(probe, "preflight", lambda *args: None)
    monkeypatch.setattr(probe, "read_optional", lambda *args: None)
    session = Mock()
    pods = Mock()
    dynamic = SimpleNamespace(resources=SimpleNamespace(get=lambda **kw: pods))
    with pytest.raises(ValueError, match="snapshot"):
        probe.resume_negative(settings, tmp_path, state, lambda: None, session, dynamic, 60, pin)
    session.client.assert_not_called()
    pods.create.assert_not_called()


def test_pinned_snapshot_is_only_used_after_api_404_and_preserves_provenance(monkeypatch, tmp_path):
    from aws.nitro_confidential import ConfidentialConfig

    pod, state, settings = resume_fixture()
    state["source_revision"] = "f30257f54"
    raw = json.dumps(pod).encode()
    (tmp_path / "positive-observation-private.json").write_bytes(raw)
    digest = probe.hashlib.sha256(raw).hexdigest()
    monkeypatch.setattr(probe, "preflight", lambda *args: None)
    monkeypatch.setattr(probe, "read_optional", lambda *args: None)
    monkeypatch.setattr(ConfidentialConfig, "validate", lambda self: None)
    execute = Mock()
    monkeypatch.setattr(probe, "execute_negative", execute)
    pods = Mock()
    dynamic = SimpleNamespace(resources=SimpleNamespace(get=lambda **kw: pods))
    session = Mock()
    session.get_credentials.return_value.get_frozen_credentials.return_value = SimpleNamespace(
        access_key="synthetic", secret_key="synthetic", token=None
    )
    probe.resume_negative(settings, tmp_path, state, lambda: None, session, dynamic, 60, digest)
    assert state["resume_positive_evidence"]["source"] == "pinned-private-snapshot-after-api-404"
    assert state["resume_positive_evidence"]["sha256"] == digest
    assert state["source_revision"] == "f30257f54"
    execute.assert_called_once()
    pods.create.assert_not_called()


def test_snapshot_cannot_replace_a_present_foreign_uid(monkeypatch, tmp_path):
    pod, state, settings = resume_fixture()
    snapshot = Mock()
    monkeypatch.setattr(probe, "verified_positive_snapshot", snapshot)
    monkeypatch.setattr(probe, "preflight", lambda *args: None)
    pod["metadata"]["uid"] = "foreign-uid"
    monkeypatch.setattr(probe, "read_optional", lambda *args: pod)
    dynamic = SimpleNamespace(resources=SimpleNamespace(get=lambda **kw: Mock()))
    session = Mock()
    with pytest.raises(ValueError, match="identity, policy, hash or success"):
        probe.resume_negative(settings, tmp_path, state, lambda: None, session, dynamic, 60, "a" * 64)
    snapshot.assert_not_called()
    session.client.assert_not_called()


def test_api_failure_does_not_enable_snapshot_fallback(monkeypatch, tmp_path):
    _pod, state, settings = resume_fixture()
    snapshot = Mock()
    monkeypatch.setattr(probe, "verified_positive_snapshot", snapshot)
    monkeypatch.setattr(probe, "preflight", lambda *args: None)

    def denied(*args):
        raise RuntimeError("API forbidden")

    monkeypatch.setattr(probe, "read_optional", denied)
    dynamic = SimpleNamespace(resources=SimpleNamespace(get=lambda **kw: Mock()))
    with pytest.raises(RuntimeError, match="API forbidden"):
        probe.resume_negative(settings, tmp_path, state, lambda: None, Mock(), dynamic, 60, "a" * 64)
    snapshot.assert_not_called()


def test_pinned_snapshot_still_requires_exact_policy_and_success(monkeypatch, tmp_path):
    pod, state, settings = resume_fixture()
    pod["status"]["phase"] = "Failed"
    raw = json.dumps(pod).encode()
    (tmp_path / "positive-observation-private.json").write_bytes(raw)
    monkeypatch.setattr(probe, "preflight", lambda *args: None)
    monkeypatch.setattr(probe, "read_optional", lambda *args: None)
    dynamic = SimpleNamespace(resources=SimpleNamespace(get=lambda **kw: Mock()))
    session = Mock()
    with pytest.raises(ValueError, match="identity, policy, hash or success"):
        probe.resume_negative(
            settings, tmp_path, state, lambda: None, session, dynamic, 60, probe.hashlib.sha256(raw).hexdigest()
        )
    session.client.assert_not_called()
