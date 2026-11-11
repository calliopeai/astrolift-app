"""Confidential preparation binds encrypted images, final admission and KMS conditions."""

import base64
import hashlib
import importlib.util
import json
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from aws import nitro_confidential as confidential
from aws.nitro_confidential_image import _write_blob

TASK = "a" * 32
ARN = "arn:aws:kms:us-west-2:123456789012:key/11111111-1111-1111-1111-111111111111"
PCR = "b" * 96


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    config = confidential.ConfidentialConfig(
        registry="123456789012.dkr.ecr.us-west-2.amazonaws.com/confidential-runs",
        key_admin_arn="arn:aws:iam::123456789012:role/controller",
        runtime_role_arn="arn:aws:iam::123456789012:role/guest",
        runtime_directory=tmp_path,
        service_account="confidential-runner",
        temporary_root=str(tmp_path),
    )
    pod = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": "agent-task-" + TASK, "namespace": "agents"},
        "spec": {
            "runtimeClassName": "confidential-sandbox",
            "restartPolicy": "Never",
            "securityContext": {"runAsUser": 42042, "runAsGroup": 42042, "runAsNonRoot": True},
            "containers": [
                {
                    "name": "agent",
                    "image": "registry.example/agent@sha256:" + "1" * 64,
                    "securityContext": {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}},
                    "env": [{"name": "ASTROLIFT_CLUSTER_KEY", "value": "synthetic-secret"}],
                }
            ],
        },
    }
    calls, checkpoints, policies, retired, generator_homes, environments = [], [], [], [], [], []
    encrypted = json.dumps(
        {
            "schemaVersion": 2,
            "layers": [
                {
                    "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip+encrypted",
                    "annotations": {
                        "org.opencontainers.image.enc.keys.provider.awskms": "d3JhcHBlZC1zeW50aGV0aWMta2V5"
                    },
                }
            ],
        }
    ).encode()
    digest = "sha256:" + hashlib.sha256(encrypted).hexdigest()

    def tool(argv, *, env, input_data=None):
        calls.append(argv)
        environments.append(dict(env))
        if argv[0] == config.skopeo:
            assert argv[1] == "--tmpdir"
            assert argv[2] == env["TMPDIR"] == env["TMP"] == env["TEMP"]
            assert Path(argv[2]).is_relative_to(Path(config.temporary_root))
            argv = [argv[0], *argv[3:]]
        if argv[1] == "login":
            Path(argv[argv.index("--authfile") + 1]).write_text(json.dumps({"auths": {}}))
        elif argv[1] == "--override-os":
            layout = Path(argv[-1].removeprefix("oci:").removesuffix(":run"))
            (layout / "blobs/sha256").mkdir(parents=True)
            image_config = {
                "architecture": "amd64",
                "os": "linux",
                "rootfs": {"diff_ids": []},
                "config": {
                    "Entrypoint": ["python3", "runner.py"],
                    "Env": ["BASE=value"],
                    "Labels": {"ai.astrolift.agent-image.contract": "7"},
                },
            }
            descriptor = _write_blob(
                layout, json.dumps(image_config).encode(), "application/vnd.oci.image.config.v1+json"
            )
            manifest = {
                "schemaVersion": 2,
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "config": descriptor,
                "layers": [],
            }
            descriptor = _write_blob(layout, json.dumps(manifest).encode(), manifest["mediaType"])
            (layout / "index.json").write_text(json.dumps({"manifests": [descriptor]}))
        elif argv[1] == "copy":
            assert "--encryption-key" in argv
            assert argv[argv.index("--encryption-key") + 1] == "provider:awskms:" + ARN
            Path(argv[argv.index("--digestfile") + 1]).write_text(digest)
        elif argv[1] == "inspect":
            return encrypted
        elif argv[1] == "--profile":
            home = Path(env["HOME"])
            generator_homes.append(home)
            assert home.name == "registry-home"
            docker_config = home / ".docker" / "config.json"
            assert docker_config.read_bytes() == Path(env["REGISTRY_AUTH_FILE"]).read_bytes()
            assert docker_config.stat().st_mode & 0o777 == 0o600
            assert home.stat().st_mode & 0o777 == 0o700
            assert "--skip-image-fetch" not in argv and "--disable-layer-pull" not in argv
            assert "--deny-logs" in argv and "--deny-termination-logs" in argv and "--deny-stream-agent-logs" in argv
            Path(argv[argv.index("--output-file-b64") + 1]).write_bytes(base64.b64encode(b"synthetic policy"))
            condition = confidential.key_policy(config, PCR)["Statement"][1]["Condition"]
            return (
                "--- Predicted PCR17 ---\n"
                + PCR
                + '\n--- KMS Key Policy Condition ---\n"Condition": '
                + json.dumps(condition)
            ).encode()
        return b""

    def key(**kwargs):
        policies.append(json.loads(kwargs["Policy"]))
        return {"KeyMetadata": {"Arn": ARN}}

    class ImageNotFound(Exception):
        pass

    def missing(**kwargs):
        raise ImageNotFound

    kms = SimpleNamespace(
        create_key=key,
        put_key_policy=lambda **kwargs: policies.append(json.loads(kwargs["Policy"])),
        schedule_key_deletion=lambda **kwargs: retired.append(kwargs),
        describe_key=lambda **kwargs: {"KeyMetadata": {"KeyState": "Enabled"}},
        list_resource_tags=lambda **kwargs: {"Tags": [{"TagKey": "astrolift-task-id", "TagValue": TASK}]},
    )
    ecr = SimpleNamespace(
        get_authorization_token=lambda: {
            "authorizationData": [
                {
                    "authorizationToken": base64.b64encode(b"AWS:synthetic-registry-password").decode(),
                    "proxyEndpoint": "https://123456789012.dkr.ecr.us-west-2.amazonaws.com",
                }
            ]
        },
        describe_images=missing,
        exceptions=SimpleNamespace(ImageNotFoundException=ImageNotFound),
    )
    monkeypatch.setattr(confidential, "command", tool)
    monkeypatch.setattr(
        confidential,
        "verified_tools",
        lambda directory, temporary, env: (temporary / "genpolicy", temporary / "keyprovider"),
    )

    def admit(value):
        result = deepcopy(value)
        container = result["spec"]["containers"][0]
        if not any(item["name"] == "AWS_CONTAINER_CREDENTIALS_FULL_URI" for item in container["env"]):
            container["env"] += [
                {"name": "AWS_STS_REGIONAL_ENDPOINTS", "value": "regional"},
                {"name": "AWS_DEFAULT_REGION", "value": "us-west-2"},
                {"name": "AWS_REGION", "value": "us-west-2"},
                {"name": "AWS_CONTAINER_CREDENTIALS_FULL_URI", "value": "http://169.254.170.23/v1/credentials"},
                {
                    "name": "AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE",
                    "value": "/var/run/secrets/pods.eks.amazonaws.com/serviceaccount/eks-pod-identity-token",
                },
            ]
        container["volumeMounts"] = [
            {
                "name": "eks-pod-identity-token",
                "readOnly": True,
                "mountPath": "/var/run/secrets/pods.eks.amazonaws.com/serviceaccount/",
            }
        ]
        result["spec"]["volumes"] = [
            {
                "name": "eks-pod-identity-token",
                "projected": {
                    "sources": [
                        {
                            "serviceAccountToken": {
                                "audience": "pods.eks.amazonaws.com",
                                "path": "eks-pod-identity-token",
                                "expirationSeconds": 86400,
                            }
                        }
                    ]
                },
            }
        ]
        return result

    arguments = dict(
        config=config,
        pod=pod,
        secret=None,
        task_id=TASK,
        callback_hash="synthetic-hash",
        kms=kms,
        ecr=ecr,
        env={},
        admit=admit,
        save=lambda value: checkpoints.append(deepcopy(value)),
    )
    return SimpleNamespace(
        arguments=arguments,
        calls=calls,
        checkpoints=checkpoints,
        policies=policies,
        retired=retired,
        generator_homes=generator_homes,
        environments=environments,
    )


def test_public_tools_are_separate_from_confidential_inputs_and_scratch(pipeline, monkeypatch):
    snapshots = []

    def public_tools(directory, temporary, env):
        workspace = Path(env["TMPDIR"])
        assert not temporary.is_relative_to(workspace)
        assert workspace.is_dir() and temporary.is_dir()
        assert temporary.stat().st_mode & 0o777 == 0o700
        for name in ("genpolicy", "keyprovider", "aws-runtime.pub"):
            (temporary / name).write_text("public verified tool material")
        snapshots.append(temporary)
        return temporary / "genpolicy", temporary / "keyprovider"

    monkeypatch.setattr(confidential, "verified_tools", public_tools)
    original_command = confidential.command

    def confined_command(argv, *, env, input_data=None):
        workspace = Path(env["TMPDIR"])
        assert workspace.is_relative_to(Path(pipeline.arguments["config"].temporary_root))
        assert env["TMP"] == env["TEMP"] == str(workspace)
        assert sorted(row.name for row in snapshots[0].iterdir()) == ["aws-runtime.pub", "genpolicy", "keyprovider"]
        assert all("synthetic-secret" not in row.read_text() for row in snapshots[0].iterdir())
        return original_command(argv, env=env, input_data=input_data)

    monkeypatch.setattr(confidential, "command", confined_command)
    confidential.prepare_run(**pipeline.arguments)
    assert snapshots and all(not root.exists() for root in snapshots)
    assert all(not Path(env["TMPDIR"]).exists() for env in pipeline.environments)


def test_public_tool_snapshot_executes_when_private_tmpfs_is_noexec(pipeline, monkeypatch):
    mountinfo = Path("/proc/self/mountinfo")
    if not mountinfo.exists() or not Path("/dev/shm").is_dir():
        pytest.skip("requires a Linux noexec memory workspace")
    mounts = [line for line in mountinfo.read_text().splitlines() if " /dev/shm " in line]
    if not mounts or "noexec" not in mounts[0].split(" - ")[0].split()[5].split(","):
        pytest.skip("the memory workspace is executable on this host")
    pipeline.arguments["config"].temporary_root = "/dev/shm"
    snapshots = []

    def public_tools(directory, temporary, env):
        blocked = Path(env["TMPDIR"]) / "public-tool-must-not-execute-here"
        shutil.copyfile("/bin/true", blocked)
        blocked.chmod(0o700)
        with pytest.raises(PermissionError):
            subprocess.run([str(blocked)], check=True, capture_output=True)
        for name in ("genpolicy", "keyprovider"):
            target = temporary / name
            shutil.copyfile("/bin/true", target)
            target.chmod(0o700)
            subprocess.run([str(target)], check=True, capture_output=True)
        snapshots.append(temporary)
        return temporary / "genpolicy", temporary / "keyprovider"

    monkeypatch.setattr(confidential, "verified_tools", public_tools)
    confidential.prepare_run(**pipeline.arguments)
    assert snapshots and all(not root.exists() for root in snapshots)


@pytest.mark.parametrize("fail_generator", [False, True])
def test_negative_policy_keeps_private_scratch_separate_and_cleans_up(pipeline, monkeypatch, fail_generator):
    script = Path(__file__).resolve().parents[3] / "scripts" / "qualify_nitro_confidential.py"
    spec = importlib.util.spec_from_file_location("negative_workspace_probe", script)
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    candidate, _ = confidential.prepare_run(**pipeline.arguments)
    snapshots, workspaces = [], []

    def public_tools(directory, temporary, env):
        workspace = Path(env["TMPDIR"])
        assert workspace.is_relative_to(Path(pipeline.arguments["config"].temporary_root))
        assert not temporary.is_relative_to(workspace)
        assert temporary.stat().st_mode & 0o777 == 0o700
        for name in ("genpolicy", "keyprovider", "aws-runtime.pub"):
            (temporary / name).write_text("public verified tool material")
        snapshots.append(temporary)
        workspaces.append(workspace)
        return temporary / "genpolicy", temporary / "keyprovider"

    monkeypatch.setattr(confidential, "verified_tools", public_tools)
    original_command = confidential.command

    def confined_command(argv, *, env, input_data=None):
        assert env["TMPDIR"] == env["TMP"] == env["TEMP"] == str(workspaces[0])
        assert sorted(row.name for row in snapshots[0].iterdir()) == ["aws-runtime.pub", "genpolicy", "keyprovider"]
        if argv[1] == "--profile" and fail_generator:
            raise RuntimeError("generator failed")
        return original_command(argv, env=env, input_data=input_data)

    monkeypatch.setattr(confidential, "command", confined_command)
    args = pipeline.arguments

    def invoke():
        return probe.regenerate_negative(candidate, args["config"], args["ecr"], {}, args["admit"])

    if fail_generator:
        with pytest.raises(RuntimeError, match="generator failed"):
            invoke()
    else:
        pod, measurement = invoke()
        assert measurement == PCR
        assert pod["metadata"]["annotations"][confidential.POLICY]
    assert snapshots and workspaces
    assert all(not root.exists() for root in snapshots + workspaces)


def test_only_encrypted_image_and_final_policy_reach_kubernetes(pipeline):
    pod, checkpoint = confidential.prepare_run(**pipeline.arguments)
    public = json.dumps({"pod": pod, "checkpoint": checkpoint})
    assert "synthetic-secret" not in public
    assert pod["spec"]["containers"][0]["env"][0] == {"name": "ASTROLIFT_CONFIDENTIAL_TASK_ID", "value": TASK}
    assert {item["name"] for item in pod["spec"]["containers"][0]["env"]} == {
        "ASTROLIFT_CONFIDENTIAL_TASK_ID",
        "AWS_STS_REGIONAL_ENDPOINTS",
        "AWS_DEFAULT_REGION",
        "AWS_REGION",
        "AWS_CONTAINER_CREDENTIALS_FULL_URI",
        "AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE",
    }
    assert pod["spec"]["serviceAccountName"] == "confidential-runner"
    assert checkpoint["pcr17"] == PCR
    assert checkpoint["attestation_verified"] is False
    assert not any("kms:Decrypt" in row["Action"] for row in pipeline.policies[0]["Statement"])
    final = pipeline.policies[-1]["Statement"][1]
    assert final["Principal"] == {"AWS": pipeline.arguments["config"].runtime_role_arn}
    assert final["Condition"]["StringEqualsIgnoreCase"] == {
        "kms:RecipientAttestation:PCR17": PCR,
        "kms:RecipientAttestation:PCR8:ServiceName": "eks.amazonaws.com",
    }
    assert not pipeline.retired


def test_policy_replacing_webhook_refuses_and_retires_key(pipeline):
    original_admit = pipeline.arguments["admit"]

    def mutate(pod):
        result = original_admit(pod)
        if confidential.POLICY in result.get("metadata", {}).get("annotations", {}):
            result["metadata"]["annotations"][confidential.POLICY] = "replaced"
        return result

    pipeline.arguments["admit"] = mutate
    with pytest.raises(ValueError, match="admission unchanged"):
        confidential.prepare_run(**pipeline.arguments)
    assert len(pipeline.retired) == 1
    assert len(pipeline.policies) == 1


def test_sidecar_admission_cannot_receive_the_secrets(pipeline):
    original_admit = pipeline.arguments["admit"]

    def mutate(pod):
        result = original_admit(pod)
        result["spec"]["containers"].append({"name": "untrusted-sidecar"})
        return result

    pipeline.arguments["admit"] = mutate
    with pytest.raises(ValueError, match="protected run boundary"):
        confidential.prepare_run(**pipeline.arguments)
    assert pipeline.retired


def test_failed_cleanup_is_durable_and_preserves_original_error(pipeline):
    def refuse(pod):
        raise ValueError("specific original admission refusal")

    def failed_cleanup(**kwargs):
        raise RuntimeError("cleanup unavailable")

    pipeline.arguments["admit"] = refuse
    pipeline.arguments["kms"].schedule_key_deletion = failed_cleanup
    with pytest.raises(ValueError, match="specific original"):
        confidential.prepare_run(**pipeline.arguments)
    assert pipeline.checkpoints[-1]["cleanup_pending"] is True


def test_scheduler_binding_does_not_break_recovery_but_changed_environment_does(pipeline):
    pod, checkpoint = confidential.prepare_run(**pipeline.arguments)
    pod["metadata"].update(uid="server-uid", resourceVersion="123", creationTimestamp="server-time")
    pod["spec"]["nodeName"] = "scheduled-node"
    pod["metadata"].setdefault("labels", {}).update(
        {
            "topology.kubernetes.io/region": "us-west-2",
            "topology.kubernetes.io/zone": "us-west-2a",
        }
    )
    assert confidential.prepared_pod_hash(pod) == checkpoint["pod_sha256"]
    assert pod["metadata"]["labels"]["topology.kubernetes.io/zone"] == "us-west-2a"
    pod["spec"]["containers"][0]["env"].append({"name": "INJECTED", "value": "value"})
    assert confidential.prepared_pod_hash(pod) != checkpoint["pod_sha256"]


def test_runtime_role_cannot_administer_the_decryption_key(pipeline):
    config = pipeline.arguments["config"]
    from dataclasses import replace

    with pytest.raises(ValueError, match="cannot administer"):
        confidential.key_policy(replace(config, key_admin_arn=config.runtime_role_arn), PCR)


def test_cleanup_refuses_a_key_owned_by_another_task(pipeline):
    _, checkpoint = confidential.prepare_run(**pipeline.arguments)
    pipeline.arguments["kms"].list_resource_tags = lambda **kwargs: {
        "Tags": [{"TagKey": "astrolift-task-id", "TagValue": "other"}]
    }
    with pytest.raises(ValueError, match="another task"):
        confidential.cleanup_run(checkpoint, kms=pipeline.arguments["kms"], ecr=pipeline.arguments["ecr"])
    assert not pipeline.retired


@pytest.mark.parametrize("failure", ["terminating", "wrong_role", "pagination", "wrong_account"])
def test_eks_confidential_prerequisites_fail_closed(pipeline, failure):
    from aws.cluster_eks import EKSClusterDriver, EKSConfig

    config = pipeline.arguments["config"]
    response = {"associations": [{"associationId": "one"}]}
    if failure == "pagination":
        response["nextToken"] = "another-page"
    eks = SimpleNamespace(
        list_pod_identity_associations=lambda **kwargs: response,
        describe_pod_identity_association=lambda **kwargs: {
            "association": {"roleArn": config.runtime_role_arn if failure != "wrong_role" else config.key_admin_arn}
        },
    )
    sts = SimpleNamespace(
        get_caller_identity=lambda: {"Account": "123456789012" if failure != "wrong_account" else "999999999999"}
    )
    driver = EKSClusterDriver(
        config=EKSConfig(region="us-west-2", cluster_name="test"), eks_client=eks, sts_client=sts, ec2_client=object()
    )
    runtime = {
        "metadata": {"name": "confidential-sandbox", "deletionTimestamp": "deleting"},
        "handler": "confidential-sandbox",
    }
    driver._k8s_cache["test"] = SimpleNamespace(get=lambda **kwargs: runtime)
    with pytest.raises(ValueError):
        driver.validate_confidential_task("test", "agents", config)


def test_already_admitted_identity_environment_can_be_revalidated(pipeline):
    final, _ = confidential.prepare_run(**pipeline.arguments)
    assert {row["name"]: row["value"] for row in final["spec"]["containers"][0]["env"]}[
        "AWS_STS_REGIONAL_ENDPOINTS"
    ] == "regional"
    confidential.validate_admitted(final, deepcopy(final), 42042, 42042)


@pytest.mark.parametrize("value", ["legacy", "", "us-west-2"])
def test_regional_sts_identity_environment_rejects_other_values(pipeline, value):
    final, _ = confidential.prepare_run(**pipeline.arguments)
    expected = deepcopy(final)
    next(row for row in final["spec"]["containers"][0]["env"] if row["name"] == "AWS_STS_REGIONAL_ENDPOINTS")[
        "value"
    ] = value
    with pytest.raises(ValueError, match="standard EKS Pod Identity"):
        confidential.validate_admitted(final, expected, 42042, 42042)


@pytest.mark.parametrize("variant", ["duplicate", "dynamic", "extra"])
def test_identity_environment_rejects_ambiguous_or_non_identity_entries(pipeline, variant):
    final, _ = confidential.prepare_run(**pipeline.arguments)
    expected = deepcopy(final)
    env = final["spec"]["containers"][0]["env"]
    if variant == "duplicate":
        env.append({"name": "AWS_REGION", "value": "us-west-2"})
        message = "duplicate environment"
    elif variant == "dynamic":
        entry = next(row for row in env if row["name"] == "AWS_STS_REGIONAL_ENDPOINTS")
        entry.pop("value")
        entry["valueFrom"] = {"fieldRef": {"fieldPath": "metadata.name"}}
        message = "only literal values"
    else:
        env.append({"name": "AWS_ROLE_ARN", "value": "arn:aws:iam::123456789012:role/other"})
        message = "non-identity environment"
    with pytest.raises(ValueError, match=message):
        confidential.validate_admitted(final, expected, 42042, 42042)


def test_private_generator_home_is_removed_without_changing_operator_config(pipeline, tmp_path):
    home = tmp_path / "operator-home"
    docker = home / ".docker"
    docker.mkdir(parents=True)
    config = docker / "config.json"
    original = b'{"auths":{"operator-registry":{"auth":"keep-existing"}}}'
    config.write_bytes(original)
    pipeline.arguments["env"] = {"HOME": str(home), "AWS_REGION": "us-west-2"}
    confidential.prepare_run(**pipeline.arguments)
    assert pipeline.arguments["env"]["HOME"] == str(home)
    assert config.read_bytes() == original
    assert pipeline.generator_homes and all(not path.exists() for path in pipeline.generator_homes)


@pytest.mark.parametrize("variant", ["task-label", "other-topology-label", "image", "policy", "spec"])
def test_recovery_hash_keeps_all_non_scheduling_authority(pipeline, variant):
    pipeline.arguments["pod"]["metadata"]["labels"] = {"astrolift.dev/task-id": TASK}
    pod, checkpoint = confidential.prepare_run(**pipeline.arguments)
    pod["metadata"]["labels"].update(
        {"topology.kubernetes.io/region": "us-west-2", "topology.kubernetes.io/zone": "us-west-2a"}
    )
    if variant == "task-label":
        pod["metadata"]["labels"]["astrolift.dev/task-id"] = "another-task"
    elif variant == "other-topology-label":
        pod["metadata"]["labels"]["topology.kubernetes.io/rack"] = "rack"
    elif variant == "image":
        pod["spec"]["containers"][0]["image"] = "changed"
    elif variant == "policy":
        pod["metadata"]["annotations"][confidential.POLICY] += "changed"
    else:
        pod["spec"]["enableServiceLinks"] = True
    assert confidential.prepared_pod_hash(pod) != checkpoint["pod_sha256"]
