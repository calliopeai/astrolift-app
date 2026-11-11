#!/usr/bin/env python3
"""Explicit live qualification runner. Run only on the owned private EKS helper.

PYTHONPATH must point to the extracted 2be74d5f4 backend/providers source.
Config JSON: region, cluster, run_id, repository, key_admin_arn, runtime_role_arn.
The caller supplies kubeconfig and runs this process in the background. No log/exec
access is needed. Pod outcomes are observations; root correlates unique-key KMS
CloudTrail success/refusal before deciding attestation qualification.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import tempfile
import time
import traceback
import uuid
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

BASE_IMAGE = "docker.io/calliopeai/astrolift-agent-base@sha256:80b2aaccda598f7d33c5fb996e92c033a044c4b1547490d08a17df5b5dd4434b"
NAMESPACE = "qualification"
SERVICE_ACCOUNT = "confidential-runner"
SOURCE_REVISION = "2be74d5f4"
SOURCE_HASHES = {
    "nitro_confidential.py": "c0a9fd64ca936c7d5c8632eb83aa1bb0fbe4dfc533dcea6c6b381167ed245d21",
    "nitro_confidential_image.py": "2d5dd02b21ee4b086f7ae2626599dc864884e9327d016da7411e78765a7a5cdb",
}


def now():
    return datetime.now(UTC).isoformat()


def write_private(path, value):
    data = json.dumps(value, indent=2, sort_keys=True).encode()
    fd = os.open(str(path) + ".new", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(str(path) + ".new", path)


def pod_observation(pod):
    meta, status = pod.get("metadata", {}), pod.get("status", {})
    containers = []
    for item in status.get("containerStatuses", []):
        row = {
            "name": item.get("name"),
            "ready": item.get("ready"),
            "restartCount": item.get("restartCount"),
        }
        for kind in ("waiting", "running", "terminated"):
            value = item.get("state", {}).get(kind)
            if value is not None:
                # Messages can carry arbitrary runtime output; keep them in private diagnostics only.
                row[kind] = {
                    k: v for k, v in value.items() if k in {"reason", "exitCode", "startedAt", "finishedAt"}
                }
        containers.append(row)
    return {
        "name": meta.get("name"),
        "uid": meta.get("uid"),
        "namespace": meta.get("namespace"),
        "phase": status.get("phase"),
        "node": pod.get("spec", {}).get("nodeName"),
        "observed_at": now(),
        "containers": containers,
    }


def read_optional(resource, name):
    from kubernetes.client.exceptions import ApiException

    try:
        return resource.get(namespace=NAMESPACE, name=name).to_dict()
    except ApiException as exc:
        if exc.status == 404:
            return None
        raise


def wait_pod(resource, name, seconds):
    deadline = time.monotonic() + seconds
    last = None
    while time.monotonic() < deadline:
        last = read_optional(resource, name)
        if last is not None and last.get("status", {}).get("phase") in {
            "Succeeded",
            "Failed",
        }:
            return last, False
        time.sleep(10)
    return last, True


def preflight(settings, session, dynamic):
    import aws.nitro_confidential as confidential

    source = Path(confidential.__file__).parent
    if any(
        hashlib.sha256((source / name).read_bytes()).hexdigest() != digest
        for name, digest in SOURCE_HASHES.items()
    ):
        raise ValueError("probe source does not match the reviewed 2be74d5f4 helpers")
    identity = session.client("sts").get_caller_identity()
    account = settings["repository"].split(".")[0]
    role_name = settings["key_admin_arn"].rsplit("/", 1)[-1]
    if identity["Account"] != account or ":assumed-role/" + role_name + "/" not in identity["Arn"]:
        raise ValueError("probe must use the configured helper key-admin role")
    eks = session.client("eks")
    cluster = eks.describe_cluster(name=settings["cluster"])["cluster"]
    if (
        not settings["run_id"].startswith("nitro-eks-")
        or cluster.get("tags", {}).get("QualificationRun") != settings["run_id"]
        or cluster["resourcesVpcConfig"].get("endpointPublicAccess")
        or not cluster["resourcesVpcConfig"].get("endpointPrivateAccess")
    ):
        raise ValueError("probe requires the tagged, private, owned qualification cluster")
    response = eks.list_pod_identity_associations(
        clusterName=settings["cluster"],
        namespace=NAMESPACE,
        serviceAccount=SERVICE_ACCOUNT,
    )
    rows = response.get("associations", [])
    if response.get("nextToken") or len(rows) != 1:
        raise ValueError("expected exactly the qualification service account association")
    association = eks.describe_pod_identity_association(
        clusterName=settings["cluster"], associationId=rows[0]["associationId"]
    )["association"]
    if association.get("roleArn") != settings["runtime_role_arn"]:
        raise ValueError("runtime role differs from qualification configuration")
    runtime = (
        dynamic.resources.get(api_version="node.k8s.io/v1", kind="RuntimeClass")
        .get(name="confidential-sandbox")
        .to_dict()
    )
    if runtime.get("handler") != "confidential-sandbox" or runtime.get("metadata", {}).get(
        "deletionTimestamp"
    ):
        raise ValueError("confidential runtime class unavailable")
    return {
        "account": identity["Account"],
        "caller_arn": identity["Arn"],
        "cluster_arn": cluster["arn"],
        "association_id": rows[0]["associationId"],
        "checked_at": now(),
    }


def regenerate_negative(candidate, config, ecr, env, admit):
    from aws.nitro_confidential import (
        POLICY,
        clean_pod,
        command,
        policy_measurement,
        validate_admitted,
        verified_tools,
    )

    first = clean_pod(admit(candidate))
    validate_admitted(first, candidate, 42042, 42042)
    with (
        tempfile.TemporaryDirectory(prefix="nitro-negative-", dir=config.temporary_root) as work,
        tempfile.TemporaryDirectory(prefix="nitro-negative-public-tools-", dir="/tmp") as public_tools,
    ):
        root = Path(work)
        env = {**env, "TMPDIR": str(root), "TMP": str(root), "TEMP": str(root)}
        generator, _ = verified_tools(config.runtime_directory, Path(public_tools), env)
        auth = root / "auth.json"
        for item in ecr.get_authorization_token()["authorizationData"]:
            password = base64.b64decode(item["authorizationToken"]).split(b":", 1)[1]
            command(
                [
                    config.skopeo,
                    "--tmpdir",
                    str(root),
                    "login",
                    "--authfile",
                    str(auth),
                    "--username",
                    "AWS",
                    "--password-stdin",
                    item["proxyEndpoint"],
                ],
                env=env,
                input_data=password,
            )
        private_home = root / "registry-home"
        private_home.mkdir(mode=0o700)
        docker = private_home / ".docker"
        docker.mkdir(mode=0o700)
        docker_config = docker / "config.json"
        docker_config.write_bytes(auth.read_bytes())
        docker_config.chmod(0o600)
        manifest, output = root / "pod.json", root / "policy.b64"
        manifest.write_text(json.dumps(first))
        report = command(
            [
                str(generator),
                "--profile",
                "confidential",
                "--deny-logs",
                "--deny-termination-logs",
                "--deny-stream-agent-logs",
                "--with-pod-identity",
                "--output-file-b64",
                str(output),
                str(manifest),
            ],
            env={
                **env,
                "HOME": str(private_home),
                "DOCKER_CONFIG": str(docker),
                "REGISTRY_AUTH_FILE": str(auth),
            },
        )
        measurement = policy_measurement(report)
        annotation = output.read_text().strip()
        if not base64.b64decode(annotation, validate=True):
            raise ValueError("empty negative policy")
        first["metadata"]["annotations"][POLICY] = annotation
        second = admit(first)
        if (
            clean_pod(second) != clean_pod(first)
            or second["metadata"]["annotations"].get(POLICY) != annotation
        ):
            raise ValueError("negative policy did not survive final admission")
        return first, measurement


def run_probe(settings, root, state, save, session, dynamic, wait_seconds):
    from aws.nitro_confidential import (
        POLICY,
        ConfidentialConfig,
        prepare_run,
        prepared_pod_hash,
    )

    pods = dynamic.resources.get(api_version="v1", kind="Pod")
    config = ConfidentialConfig(
        registry=settings["repository"],
        key_admin_arn=settings["key_admin_arn"],
        runtime_role_arn=settings["runtime_role_arn"],
        runtime_directory=Path(settings.get("runtime_directory", "/opt/astrolift/nitro")),
        service_account=SERVICE_ACCOUNT,
        temporary_root="/dev/shm",
        resource_tags={"QualificationRun": settings["run_id"]},
    )
    config.validate()
    state["preflight"] = preflight(settings, session, dynamic)
    state["stage"] = "preparing_positive"
    save()
    kms, ecr = session.client("kms"), session.client("ecr")
    credentials = session.get_credentials().get_frozen_credentials()
    env = {
        **os.environ,
        "AWS_ACCESS_KEY_ID": credentials.access_key,
        "AWS_SECRET_ACCESS_KEY": credentials.secret_key,
        "AWS_REGION": settings["region"],
        "AWS_DEFAULT_REGION": settings["region"],
    }
    if credentials.token:
        env["AWS_SESSION_TOKEN"] = credentials.token
    else:
        env.pop("AWS_SESSION_TOKEN", None)
    task_id = state["task_id"]
    name = "nitro-positive-" + task_id[:20]
    value = secrets.token_urlsafe(48)
    digest = hashlib.sha256(value.encode()).hexdigest()
    assertion = (
        "import hashlib,hmac,os; "
        "assert hmac.compare_digest(hashlib.sha256(os.environ['ASTROLIFT_QUALIFICATION_SECRET'].encode()).hexdigest(), "
        + repr(digest)
        + ")"
    )
    # This plaintext command/environment remains in memory and the private encrypted-layer workspace only.
    candidate = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": name,
            "namespace": NAMESPACE,
            "labels": {
                "QualificationRun": settings["run_id"],
                "astrolift.dev/task-id": str(uuid.UUID(task_id)),
            },
        },
        "spec": {
            "runtimeClassName": "confidential-sandbox",
            "restartPolicy": "Never",
            "automountServiceAccountToken": False,
            "activeDeadlineSeconds": wait_seconds,
            "securityContext": {
                "runAsUser": 42042,
                "runAsGroup": 42042,
                "runAsNonRoot": True,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "containers": [
                {
                    "name": "agent",
                    "image": BASE_IMAGE,
                    "command": ["python3", "-c", assertion],
                    "env": [
                        {
                            "name": "ASTROLIFT_QUALIFICATION_SECRET",
                            "valueFrom": {
                                "secretKeyRef": {
                                    "name": "memory-only-run-secret",
                                    "key": "dummy",
                                }
                            },
                        }
                    ],
                    "resources": {
                        "requests": {"cpu": "500m", "memory": "512Mi"},
                        "limits": {"cpu": "1", "memory": "1Gi"},
                    },
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                        "capabilities": {"drop": ["ALL"]},
                    },
                }
            ],
        },
    }
    secret = {
        "metadata": {"name": "memory-only-run-secret"},
        "stringData": {"dummy": value},
    }

    def admit(manifest):
        return pods.create(namespace=NAMESPACE, body=manifest, dry_run="All").to_dict()

    def checkpoint(row):
        state["checkpoint"] = deepcopy(row)
        if row.get("key_arn") and row["key_arn"] not in state["key_arns"]:
            state["key_arns"].append(row["key_arn"])
        save()

    positive, checkpoint_data = prepare_run(
        config=config,
        pod=candidate,
        secret=secret,
        task_id=task_id,
        callback_hash=hashlib.sha256(task_id.encode()).hexdigest(),
        kms=kms,
        ecr=ecr,
        env=env,
        admit=admit,
        save=checkpoint,
    )
    del secret, value, assertion, candidate
    state["stage"] = "positive_prepared"
    state["positive"] = {
        "name": name,
        "image": checkpoint_data["image"],
        "pcr17": checkpoint_data["pcr17"],
        "policy_sha256": hashlib.sha256(positive["metadata"]["annotations"][POLICY].encode()).hexdigest(),
        "prepared_pod_hash": prepared_pod_hash(positive),
    }
    state["audit_window_started_at"] = now()
    save()
    created = pods.create(namespace=NAMESPACE, body=positive).to_dict()
    state["positive"].update(pod_observation(created))
    save()
    observed, timed_out = wait_pod(pods, name, wait_seconds)
    if observed:
        write_private(root / "positive-observation-private.json", observed)
        state["positive"].update(pod_observation(observed))
    state["positive"]["timed_out"] = timed_out
    state["positive"]["assertion_succeeded"] = bool(
        observed
        and observed.get("status", {}).get("phase") == "Succeeded"
        and any(
            item.get("state", {}).get("terminated", {}).get("exitCode") == 0
            for item in observed.get("status", {}).get("containerStatuses", [])
        )
    )
    save()
    if not state["positive"]["assertion_succeeded"]:
        raise RuntimeError("positive secret assertion has not succeeded; negative experiment withheld")
    execute_negative(positive, config, env, admit, kms, ecr, root, state, save, pods, wait_seconds)


def canonical_key_policy(value):
    """Normalize only the observed KMS Action scalar/singleton-list encoding."""
    result = deepcopy(value)
    statements = result.get("Statement")
    if not isinstance(statements, list) or any(not isinstance(row, dict) for row in statements):
        raise ValueError("key policy must contain explicit statement objects")
    for statement in statements:
        action = statement.get("Action")
        if isinstance(action, str):
            statement["Action"] = [action]
        elif not isinstance(action, list) or not all(isinstance(entry, str) for entry in action):
            raise ValueError("key policy has unsupported action structure")
    return result


def execute_negative(positive, config, env, admit, kms, ecr, root, state, save, pods, wait_seconds):
    from aws.nitro_confidential import POLICY, clean_pod, key_policy, prepared_pod_hash

    checkpoint_data, task_id = state["checkpoint"], state["task_id"]
    # Do not schedule deletion, remove the image, or change the grant before negative execution.
    key_arn = checkpoint_data["key_arn"]
    original_policy = json.loads(kms.get_key_policy(KeyId=key_arn, PolicyName="default")["Policy"])
    if kms.describe_key(KeyId=key_arn)["KeyMetadata"]["KeyState"] != "Enabled" or canonical_key_policy(
        original_policy
    ) != canonical_key_policy(key_policy(config, checkpoint_data["pcr17"])):
        raise ValueError("positive key no longer has the original enabled attestation grant")
    grant_hash = hashlib.sha256(json.dumps(original_policy, sort_keys=True).encode()).hexdigest()
    negative_candidate = clean_pod(positive)
    negative_candidate["spec"].pop("nodeName", None)
    negative_candidate["metadata"]["name"] = "nitro-negative-" + task_id[:20]
    negative_candidate["spec"]["containers"][0]["env"].append(
        {"name": "QUALIFICATION_PUBLIC_VARIANT", "value": "negative"}
    )
    negative, negative_pcr = regenerate_negative(negative_candidate, config, ecr, env, admit)
    if negative_pcr == checkpoint_data["pcr17"]:
        raise ValueError("changed final policy did not change PCR17")
    state["negative"] = {
        "name": negative["metadata"]["name"],
        "image": checkpoint_data["image"],
        "pcr17": negative_pcr,
        "prepared_pod_hash": prepared_pod_hash(negative),
        "policy_sha256": hashlib.sha256(negative["metadata"]["annotations"][POLICY].encode()).hexdigest(),
        "controlled_delta": ["metadata.name", "env:QUALIFICATION_PUBLIC_VARIANT"],
        "original_grant_sha256": grant_hash,
    }
    state["stage"] = "negative_prepared"
    write_private(root / "negative-final-pod.json", negative)
    save()
    if json.loads(kms.get_key_policy(KeyId=key_arn, PolicyName="default")["Policy"]) != original_policy:
        raise ValueError("key grant changed before negative submission")
    state["negative"]["submitted_at"] = now()
    save()
    created = pods.create(namespace=NAMESPACE, body=negative).to_dict()
    state["negative"].update(pod_observation(created))
    save()
    observed, timed_out = wait_pod(pods, state["negative"]["name"], wait_seconds)
    if observed:
        write_private(root / "negative-observation-private.json", observed)
        state["negative"].update(pod_observation(observed))
    state["negative"]["timed_out"] = timed_out
    state["negative"]["key_enabled_after"] = (
        kms.describe_key(KeyId=key_arn)["KeyMetadata"]["KeyState"] == "Enabled"
    )
    state["negative"]["original_grant_unchanged"] = (
        json.loads(kms.get_key_policy(KeyId=key_arn, PolicyName="default")["Policy"]) == original_policy
    )
    state["audit_window_finished_at"] = now()
    state["stage"] = "observations_complete_kms_audit_required"
    if not state["negative"]["key_enabled_after"] or not state["negative"]["original_grant_unchanged"]:
        raise ValueError("negative observation lost controlled key/grant state")
    if observed and observed.get("status", {}).get("phase") == "Succeeded":
        raise RuntimeError("negative policy unexpectedly ran successfully")
    save()


def verified_positive_snapshot(root, digest):
    """Read only the fixed private observation whose exact digest the operator pinned."""
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
    ):
        raise ValueError("positive snapshot requires an explicit lowercase SHA256")
    path = root / "positive-observation-private.json"
    if path.is_symlink():
        raise ValueError("positive snapshot must not be a symlink")
    raw = path.read_bytes()
    if not secrets.compare_digest(hashlib.sha256(raw).hexdigest(), digest):
        raise ValueError("positive snapshot SHA256 differs from the operator pin")
    return json.loads(raw)


def resume_negative(
    settings, root, state, save, session, dynamic, wait_seconds, positive_snapshot_sha256=None
):
    from aws.nitro_confidential import POLICY, ConfidentialConfig, prepared_pod_hash

    if state.get("source_revision") not in {SOURCE_REVISION, "374066149", "f30257f54"} or state.get(
        "negative"
    ):
        raise ValueError("resume requires this source checkpoint and no prior negative attempt")
    preflight(settings, session, dynamic)
    saved, checkpoint = state["positive"], state["checkpoint"]
    pods = dynamic.resources.get(api_version="v1", kind="Pod")
    positive = read_optional(pods, saved["name"])
    evidence = {"source": "live-api", "checked_at": now()}
    if positive is None:
        # read_optional returns None only for an authoritative API 404.
        # Never replace a present, changed Pod with an older observation.
        if positive_snapshot_sha256 is None:
            raise ValueError("positive Pod is absent; refusing negative resume without a pinned snapshot")
        positive = verified_positive_snapshot(root, positive_snapshot_sha256)
        evidence = {
            "source": "pinned-private-snapshot-after-api-404",
            "sha256": positive_snapshot_sha256,
            "file": "positive-observation-private.json",
            "checked_at": now(),
        }
    meta = positive["metadata"]
    containers = positive.get("spec", {}).get("containers", [])
    statuses = positive.get("status", {}).get("containerStatuses", [])
    if (
        meta.get("uid") != saved.get("uid")
        or not saved.get("uid")
        or meta.get("deletionTimestamp")
        or meta.get("labels", {}).get("QualificationRun") != state["run_id"]
        or meta.get("labels", {}).get("astrolift.dev/task-id") != str(uuid.UUID(state["task_id"]))
        or checkpoint.get("task_id") != state["task_id"]
        or checkpoint.get("image") != saved.get("image")
        or checkpoint.get("pod_sha256") != saved.get("prepared_pod_hash")
        or len(containers) != 1
        or containers[0].get("image") != checkpoint["image"]
        or meta.get("annotations", {}).get(POLICY) != checkpoint.get("policy")
        or hashlib.sha256(checkpoint.get("policy", "").encode()).hexdigest() != saved.get("policy_sha256")
        or prepared_pod_hash(positive) != saved.get("prepared_pod_hash")
        or positive.get("status", {}).get("phase") != "Succeeded"
        or not saved.get("assertion_succeeded")
        or len(statuses) != 1
        or statuses[0].get("name") != containers[0].get("name")
        or statuses[0].get("state", {}).get("terminated", {}).get("exitCode") != 0
    ):
        raise ValueError("positive Pod identity, policy, hash or success differs; refusing resume")
    config = ConfidentialConfig(
        registry=settings["repository"],
        key_admin_arn=settings["key_admin_arn"],
        runtime_role_arn=settings["runtime_role_arn"],
        runtime_directory=Path(settings.get("runtime_directory", "/opt/astrolift/nitro")),
        service_account=SERVICE_ACCOUNT,
        temporary_root="/dev/shm",
        resource_tags={"QualificationRun": settings["run_id"]},
    )
    config.validate()
    if checkpoint["registry"] != config.registry:
        raise ValueError("resume registry differs from prepared checkpoint")
    credentials = session.get_credentials().get_frozen_credentials()
    env = {
        **os.environ,
        "AWS_ACCESS_KEY_ID": credentials.access_key,
        "AWS_SECRET_ACCESS_KEY": credentials.secret_key,
        "AWS_REGION": settings["region"],
        "AWS_DEFAULT_REGION": settings["region"],
    }
    if credentials.token:
        env["AWS_SESSION_TOKEN"] = credentials.token
    else:
        env.pop("AWS_SESSION_TOKEN", None)

    def admit(manifest):
        return pods.create(namespace=NAMESPACE, body=manifest, dry_run="All").to_dict()

    state["resume_negative_started_at"] = now()
    state["resume_helper_revision"] = SOURCE_REVISION
    state["resume_positive_evidence"] = evidence
    if state.get("failure"):
        state.setdefault("prior_failures", []).append(state.pop("failure"))
    save()
    execute_negative(
        positive,
        config,
        env,
        admit,
        session.client("kms"),
        session.client("ecr"),
        root,
        state,
        save,
        pods,
        wait_seconds,
    )


def cleanup(settings, state, save, session, dynamic):
    from aws.nitro_confidential import POLICY, cleanup_run, prepared_pod_hash

    pods = dynamic.resources.get(api_version="v1", kind="Pod")
    for label in ("negative", "positive"):
        saved = state.get(label)
        if not saved:
            continue
        actual = read_optional(pods, saved["name"])
        if actual is None:
            continue
        meta = actual["metadata"]
        if (
            meta.get("labels", {}).get("QualificationRun") != state["run_id"]
            or (saved.get("uid") is not None and meta.get("uid") != saved["uid"])
            or meta.get("labels", {}).get("astrolift.dev/task-id") != str(uuid.UUID(state["task_id"]))
            or actual["spec"]["containers"][0].get("image") != saved["image"]
            or prepared_pod_hash(actual) != saved["prepared_pod_hash"]
            or hashlib.sha256(meta.get("annotations", {}).get(POLICY, "").encode()).hexdigest()
            != saved["policy_sha256"]
        ):
            raise ValueError("refusing cleanup of a replaced or mutated qualification Pod")
        pods.delete(
            namespace=NAMESPACE,
            name=saved["name"],
            body={
                "apiVersion": "v1",
                "kind": "DeleteOptions",
                "preconditions": {"uid": meta["uid"]},
            },
        )
        deadline = time.monotonic() + 180
        while read_optional(pods, saved["name"]) is not None:
            if time.monotonic() >= deadline:
                raise RuntimeError("qualification Pod deletion remains pending")
            time.sleep(10)
    row = state.get("checkpoint")
    if row:
        cleanup_run(row, kms=session.client("kms"), ecr=session.client("ecr"))
        key = session.client("kms").describe_key(KeyId=row["key_arn"])["KeyMetadata"]
        if key["KeyState"] != "PendingDeletion":
            raise RuntimeError("key retirement is not verified")
        state["cleanup"] = {
            "completed_at": now(),
            "key_arn": row["key_arn"],
            "key_state": key["KeyState"],
        }
    state["stage"] = "cleanup_complete"
    save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--state-directory", type=Path, required=True)
    parser.add_argument("--kubeconfig")
    parser.add_argument("--wait-seconds", type=int, default=900)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--cleanup", action="store_true")
    mode.add_argument("--resume-negative", action="store_true")
    parser.add_argument("--positive-snapshot-sha256")
    args = parser.parse_args()
    if args.positive_snapshot_sha256 and not args.resume_negative:
        parser.error("positive-snapshot-sha256 requires --resume-negative")
    if not 60 <= args.wait_seconds <= 1800:
        parser.error("wait-seconds must be between 60 and 1800")
    os.umask(0o077)
    root = args.state_directory
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    settings = json.loads(args.config.read_text())
    import boto3
    from kubernetes import client, config, dynamic

    session = boto3.Session(region_name=settings["region"])
    config.load_kube_config(config_file=args.kubeconfig)
    dynamic_client = dynamic.DynamicClient(client.ApiClient())
    state_path = root / "state.json"
    if args.cleanup or args.resume_negative:
        state = json.loads(state_path.read_text())
        if state["run_id"] != settings["run_id"] or state["cluster"] != settings["cluster"]:
            raise ValueError("cleanup configuration differs from recorded authority")
    else:
        if state_path.exists():
            raise ValueError(
                "state already exists; preserve artifacts and use a fresh directory or --cleanup"
            )
        state = {
            "source_revision": SOURCE_REVISION,
            "run_id": settings["run_id"],
            "cluster": settings["cluster"],
            "started_at": now(),
            "task_id": uuid.uuid4().hex,
            "key_arns": [],
            "stage": "starting",
            "attestation_verified": False,
            "kms_audit_required": True,
            "base_image": BASE_IMAGE,
        }

    def save():
        state["updated_at"] = now()
        write_private(state_path, state)

    save()
    code = 0
    try:
        if args.cleanup:
            cleanup(settings, state, save, session, dynamic_client)
        elif args.resume_negative:
            resume_negative(
                settings,
                root,
                state,
                save,
                session,
                dynamic_client,
                args.wait_seconds,
                positive_snapshot_sha256=args.positive_snapshot_sha256,
            )
        else:
            run_probe(settings, root, state, save, session, dynamic_client, args.wait_seconds)
    except BaseException as exc:
        state["failure"] = {
            "type": type(exc).__name__,
            "at": now(),
            "stage": state["stage"],
        }
        diagnostic = traceback.format_exc().encode()
        if hasattr(exc, "diagnostics"):
            diagnostic += b"\nPRIVATE TOOL STDERR\n" + exc.diagnostics
        fd = os.open(
            root / "diagnostics-private.log",
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            0o600,
        )
        with os.fdopen(fd, "wb") as stream:
            stream.write(diagnostic)
        save()
        code = 1
    summary = {key: value for key, value in state.items() if key != "checkpoint"}
    summary["state_path"] = str(state_path)
    summary["checkpoint_metadata"] = {
        key: value for key, value in state.get("checkpoint", {}).items() if key != "policy"
    }
    print(json.dumps(summary, sort_keys=True))
    raise SystemExit(code)


if __name__ == "__main__":
    main()
