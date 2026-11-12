#!/usr/bin/env python3
"""Conflict-only negative adapter. Default: read-only inspect; never changes KMS policy."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import tempfile
import uuid
from pathlib import Path

from qualify_nitro_confidential import canonical_key_policy, now, regenerate_negative

CLUSTER_GUID = "01a05978-b271-77f3-a56e-6d92742c7f99"
CLUSTER_NAME = "conflict-astrolift"
NAMESPACE = "astrolift-agents-conflict"
LABEL = "astrolift.dev/nitro-negative-probe"
PROBE = Path(__file__).with_name("nitro_app_qualification_probe.py")
BASE_IMAGE = "docker.io/calliopeai/astrolift-agent-base@sha256:80b2aaccda598f7d33c5fb996e92c033a044c4b1547490d08a17df5b5dd4434b"


def private_directory(path):
    metadata = path.parent.stat()
    if (
        path.parent.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_mode & 0o077
        or metadata.st_uid != os.geteuid()
    ):
        raise ValueError("receipt parent must be a private owned directory")


def read_receipt(path, pin):
    private_directory(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077 or metadata.st_uid != os.geteuid():
            raise ValueError("receipt must be a private owned regular file")
        raw = stream.read()
    if hashlib.sha256(raw).hexdigest() != pin:
        raise ValueError("existing receipt requires its exact SHA256 pin")
    return json.loads(raw)


def write_receipt(path, state):
    private_directory(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".nitro-negative-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(state, indent=2, sort_keys=True).encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def verify_binding_authority(task):
    from astrolift_agents.models import AgentSecretBindingOverride
    from astrolift_dispatch.agent_secrets import effective_secret_refs

    spec = task.environment_spec
    if spec is None:
        raise ValueError("qualification needs an environment spec")
    if any(row["env_var"] == "QUALIFICATION_PUBLIC_VARIANT" for row in effective_secret_refs(spec)):
        raise ValueError("effective secret binding overrides the negative callback guard")
    # Removing/renaming a binding after sealing cannot remove it from the capsule.
    # Include tombstones and soft-deleted rows, not just the current effective refs.
    history = AgentSecretBindingOverride.all_objects.filter(environment_spec_id=spec.pk).only(
        "env_var", "created_at", "updated_at", "deleted_at"
    )
    if any(
        row.env_var == "QUALIFICATION_PUBLIC_VARIANT"
        or row.created_at > task.created_at
        or row.updated_at > task.created_at
        or (row.deleted_at is not None and row.deleted_at > task.created_at)
        for row in history
    ):
        raise ValueError("secret binding history contains the negative guard or changed after dispatch")


def verify_positive(task, pod, config, expected_uid, expected_checkpoint):
    from aws.nitro_confidential import POLICY, prepared_pod_hash

    target = task.dispatch_target
    checkpoint = target.get("nitro_confidential", {})
    compact = uuid.UUID(str(task.guid)).hex
    meta, spec = pod["metadata"], pod["spec"]
    containers = spec.get("containers", [])
    if (
        task.status != "running"
        or task.cancel_requested_at
        or task.organization.slug != "conflict"
        or target.get("backend") != "k8s_confidential_pod"
        or target.get("cluster_guid") != CLUSTER_GUID
        or target.get("namespace") != NAMESPACE
        or digest(checkpoint) != expected_checkpoint
        or checkpoint.get("cleanup_complete")
        or checkpoint.get("task_id") != compact
        or checkpoint.get("callback_hash") != task.callback_token_hash
        or checkpoint.get("registry") != config.registry
        or not expected_uid
        or meta.get("uid") != expected_uid
        or meta.get("name") != "agent-task-" + compact
        or meta.get("namespace") != NAMESPACE
        or meta.get("deletionTimestamp")
        or meta.get("labels", {}).get("astrolift.dev/task-id") != str(task.guid)
        or pod.get("status", {}).get("phase") != "Running"
        or prepared_pod_hash(pod) != checkpoint.get("pod_sha256")
        or meta.get("annotations", {}).get(POLICY) != checkpoint.get("policy")
        or not checkpoint.get("policy")
        or spec.get("runtimeClassName") != "confidential-sandbox"
        or spec.get("serviceAccountName") != config.service_account
        or len(containers) != 1
        or containers[0].get("image") != checkpoint.get("image")
    ):
        raise ValueError("positive App checkpoint/Pod identity or running state differs")
    environment = containers[0].get("env", [])
    names = [row.get("name") for row in environment]
    if (
        len(set(names)) != len(names)
        or "QUALIFICATION_PUBLIC_VARIANT" in names
        or next(
            (row.get("value") for row in environment if row.get("name") == "ASTROLIFT_CONFIDENTIAL_TASK_ID"),
            None,
        )
        != compact
    ):
        raise ValueError("public capsule identity/negative guard differs")
    security = spec.get("securityContext", {})
    container_security = containers[0].get("securityContext", {})
    if (
        any(security.get(name) != 42042 for name in ("runAsUser", "runAsGroup"))
        or "fsGroup" in security
        or security.get("runAsNonRoot") is not True
        or security.get("seccompProfile") != {"type": "RuntimeDefault"}
        or container_security.get("allowPrivilegeEscalation") is not False
        or container_security.get("privileged")
        or container_security.get("capabilities") != {"drop": ["ALL"]}
    ):
        raise ValueError("qualification requires the reviewed non-root UID/GID and sandbox security context")
    if task.brief is None or task.agent_definition is None or task.environment_spec is None:
        raise ValueError("qualification needs a registered workload, Brief and environment")
    primary = task.agent_definition.containers.filter(is_primary=True).get()
    if (
        primary.image_ref != BASE_IMAGE
        or primary.command != ["python3", "-c"]
        or primary.args != [PROBE.read_text()]
    ):
        raise ValueError("registered command is not the reviewed callback-guarded probe")
    if (
        primary.updated_at > task.created_at
        or task.environment_spec.updated_at > task.created_at
        or "QUALIFICATION_PUBLIC_VARIANT"
        in json.dumps(
            [
                primary.env,
                task.environment_spec.env_vars,
                task.environment_spec.secret_refs,
                task.brief.manifest_snapshot,
            ]
        )
    ):
        raise ValueError("probe definition changed after dispatch or overrides negative guard")
    findings = (task.result or {}).get("findings", [])
    if not any(
        isinstance(row, dict)
        and row.get("sealed_inputs_ok") is True
        and row.get("uid") == 42042
        and row.get("gid") == 42042
        and row.get("brief_id") == str(task.brief.guid)
        and row.get("brief_hash") == task.brief.content_hash
        and row.get("qualification")
        for row in findings
    ):
        raise ValueError("positive App guest finding is missing")
    return checkpoint


def verify_key(checkpoint, config, kms, ecr):
    from aws.nitro_confidential import key_policy

    key = checkpoint["key_arn"]
    account, region = config.registry.split(".")[0], config.registry.split(".")[3]
    if not key.startswith(f"arn:aws:kms:{region}:{account}:key/"):
        raise ValueError("key account/region differs")
    metadata = kms.describe_key(KeyId=key)["KeyMetadata"]
    tags = kms.list_resource_tags(KeyId=key)
    policy = canonical_key_policy(json.loads(kms.get_key_policy(KeyId=key, PolicyName="default")["Policy"]))
    if (
        metadata.get("Arn") != key
        or metadata.get("KeyState") != "Enabled"
        or tags.get("Truncated")
        or {row["TagKey"]: row["TagValue"] for row in tags["Tags"]}.get("astrolift-task-id")
        != checkpoint["task_id"]
        or policy != canonical_key_policy(key_policy(config, checkpoint["pcr17"]))
    ):
        raise ValueError("original Enabled attestation key ownership/grant differs")
    images = ecr.describe_images(
        registryId=account,
        repositoryName=config.registry.split("/", 1)[1],
        imageIds=[{"imageTag": checkpoint["task_id"]}],
    )["imageDetails"]
    if (
        len(images) != 1
        or images[0].get("imageTags") != [checkpoint["task_id"]]
        or checkpoint["image"] != config.registry + "@" + images[0]["imageDigest"]
    ):
        raise ValueError("encrypted image ownership/digest differs")
    return digest(policy)


def candidate_for(pod, token, seconds):
    from aws.nitro_confidential import clean_pod

    candidate = clean_pod(pod)
    meta, spec = candidate["metadata"], candidate["spec"]
    for name in (
        "ownerReferences",
        "finalizers",
        "generateName",
        "deletionTimestamp",
        "deletionGracePeriodSeconds",
    ):
        meta.pop(name, None)
    meta["name"] = "nitro-app-negative-" + token
    meta["labels"] = {"astrolift.dev/task-id": str(uuid.UUID(token)), LABEL: token}
    spec.pop("nodeName", None)
    spec["activeDeadlineSeconds"] = seconds
    spec["containers"][0]["env"].append({"name": "QUALIFICATION_PUBLIC_VARIANT", "value": "negative"})
    return candidate


def inspect(task, pod, config, kms, ecr, uid, checkpoint_hash):
    checkpoint = verify_positive(task, pod, config, uid, checkpoint_hash)
    return checkpoint, verify_key(checkpoint, config, kms, ecr)


def submit(load, config, kms, ecr, k8s, env, uid, checkpoint_hash, state, save, seconds=120):
    if state:
        raise ValueError("receipt already exists; refuse duplicate submission")
    if not 30 <= seconds <= 300:
        raise ValueError("negative deadline must be 30..300 seconds")
    task, pod = load()
    checkpoint, grant = inspect(task, pod, config, kms, ecr, uid, checkpoint_hash)
    token = uuid.uuid4().hex
    candidate = candidate_for(pod, token, seconds)
    negative, pcr = regenerate_negative(
        candidate, config, ecr, env, lambda manifest: k8s.admit_pod(namespace=NAMESPACE, manifest=manifest)
    )
    from aws.nitro_confidential import POLICY, prepared_pod_hash

    if pcr == checkpoint["pcr17"]:
        raise ValueError("negative did not change predicted PCR17")
    task, pod = load()
    _, current = inspect(task, pod, config, kms, ecr, uid, checkpoint_hash)
    if current != grant:
        raise ValueError("original grant changed during preparation")
    state.update(
        cluster_guid=CLUSTER_GUID,
        namespace=NAMESPACE,
        task_id=str(task.guid),
        positive_uid=uid,
        checkpoint_sha256=checkpoint_hash,
        token=token,
        name=negative["metadata"]["name"],
        pod_sha256=prepared_pod_hash(negative),
        original_grant_sha256=grant,
        predicted_pcr17=pcr,
        policy_sha256=hashlib.sha256(negative["metadata"]["annotations"][POLICY].encode()).hexdigest(),
        image=checkpoint["image"],
        key_arn=checkpoint["key_arn"],
        prepared_at=now(),
        stage="prepared",
    )
    save()
    # The SDK's create_manifest discards the response UID; bind to this POST,
    # never to a later GET that may already describe a same-name replacement.
    k8s._refresh_token()
    actual = k8s._to_dict(k8s._resource_for("v1", "Pod").create(namespace=NAMESPACE, body=negative))
    created_uid = actual.get("metadata", {}).get("uid")
    if not created_uid:
        raise ValueError("create response has no UID; receipt retained without adoption")
    state.update(uid=created_uid, submitted_at=now(), stage="submitted_audit_required")
    save()
    verify_clone(actual, state)
    if verify_key(checkpoint, config, kms, ecr) != grant:
        raise ValueError("original grant changed after submission; receipt retained for cleanup")


def verify_clone(pod, state):
    from aws.nitro_confidential import prepared_pod_hash

    meta = pod.get("metadata", {})
    if (
        state.get("cluster_guid") != CLUSTER_GUID
        or state.get("namespace") != NAMESPACE
        or meta.get("namespace") != NAMESPACE
        or meta.get("name") != "nitro-app-negative-" + state["token"]
        or meta.get("name") != state.get("name")
        or meta.get("labels", {}).get(LABEL) != state["token"]
        or not meta.get("uid")
        or meta.get("uid") != state.get("uid")
        or prepared_pod_hash(pod) != state.get("pod_sha256")
        or meta.get("ownerReferences")
    ):
        raise ValueError("negative clone ownership/UID/hash differs")


def container_creation_refused(row):
    """A containerd StartError can terminate a record before the guest starts.

    This recognizes only the explicit image-preparation KMS refusal shape. It
    does not establish an attestation denial; original-key audit correlation is
    still required. Missing or contradictory execution evidence fails closed.
    """
    state = row.get("state", {})
    terminated = state.get("terminated", {})
    message = terminated.get("message", "")
    return (
        set(state) == {"terminated"}
        and row.get("started") is False
        and row.get("restartCount") == 0
        and not row.get("lastState")
        and terminated.get("reason") == "StartError"
        and terminated.get("exitCode") == 128
        and terminated.get("startedAt") == "1970-01-01T00:00:00Z"
        and bool(terminated.get("finishedAt"))
        and "failed to create containerd task" in message
        and "prepare_image: KMS Decrypt failed" in message
        and "AccessDeniedException" in message
    )


def observe(k8s, state):
    pod = k8s.get(kind="Pod", namespace=NAMESPACE, name=state["name"])
    if pod is None:
        return {"absent": True, "attestation": "inconclusive_audit_required"}
    verify_clone(pod, state)
    status = pod.get("status", {})
    statuses = status.get("containerStatuses", []) + status.get("initContainerStatuses", [])
    guest = status.get("phase") in ("Running", "Succeeded") or any(
        row.get("restartCount", 0) > 0
        or bool(row.get("lastState"))
        or (
            any(name in row.get("state", {}) for name in ("running", "terminated"))
            and not container_creation_refused(row)
        )
        for row in statuses
    )
    return {
        "phase": pod.get("status", {}).get("phase"),
        "guest_started": guest,
        "attestation": "FAILED_guest_started" if guest else "inconclusive_audit_required",
    }


def observe_controlled(load, config, kms, ecr, k8s, uid, checkpoint_hash, state):
    task, positive = load()
    checkpoint, grant = inspect(task, positive, config, kms, ecr, uid, checkpoint_hash)
    if grant != state["original_grant_sha256"]:
        raise ValueError("original grant changed before negative observation")
    result = observe(k8s, state)
    if verify_key(checkpoint, config, kms, ecr) != grant:
        raise ValueError("original grant changed during negative observation")
    return {**result, "key_enabled": True, "original_grant_unchanged": True, "observed_at": now()}


def cleanup(k8s, state):
    if not state.get("uid"):
        raise ValueError("no recorded UID; never adopt a clone for deletion")
    pod = k8s.get(kind="Pod", namespace=NAMESPACE, name=state["name"])
    if pod is None:
        return
    verify_clone(pod, state)
    k8s.delete(
        kind="Pod",
        namespace=NAMESPACE,
        name=state["name"],
        uid=state["uid"],
        resource_version=pod["metadata"]["resourceVersion"],
    )


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True)
    parser.add_argument("--positive-uid", required=True)
    parser.add_argument("--checkpoint-sha256", required=True)
    parser.add_argument("--action", choices=("inspect", "submit", "observe", "cleanup"), default="inspect")
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--receipt-sha256")
    parser.add_argument("--seconds", type=int, default=120)
    return parser.parse_args(argv)


def main():
    args = parse_args()
    import django

    django.setup()
    from aws.session import aws_session

    from astrolift_agents.models import AgentTask
    from astrolift_clusters.models import TenantCluster
    from astrolift_clusters.nitro import confidential_config
    from core.cluster_management import _context_for_cluster, _driver_for_cluster

    cluster = TenantCluster.all_objects.get(guid=CLUSTER_GUID, deleted_at__isnull=True)
    driver = _driver_for_cluster(cluster)
    if driver._config.cluster_name != CLUSTER_NAME or driver._config.region != "us-west-2":
        raise ValueError("provider is not the expected Conflict EKS cluster")
    config = confidential_config(cluster)
    context = _context_for_cluster(cluster)
    description = driver._eks.describe_cluster(name=CLUSTER_NAME)["cluster"]
    if (
        description.get("status") != "ACTIVE"
        or tuple(map(int, description["version"].split("."))) < (1, 36)
        or description["arn"].split(":")[4] != config.registry.split(".")[0]
    ):
        raise ValueError("Conflict EKS account/version/state not ready")
    driver.validate_confidential_task(context.slug, NAMESPACE, config)
    k8s = driver._k8s(context.slug)
    session = aws_session(region=driver._config.region, credential=driver._config.credential)
    kms, ecr = session.client("kms"), session.client("ecr")

    def load():
        task = AgentTask.all_objects.select_related(
            "organization", "brief", "agent_definition", "environment_spec"
        ).get(guid=args.task, deleted_at__isnull=True)
        verify_binding_authority(task)
        return task, k8s.get(kind="Pod", namespace=NAMESPACE, name="agent-task-" + uuid.UUID(args.task).hex)

    state = {}
    if args.receipt.exists():
        state = read_receipt(args.receipt, args.receipt_sha256)
        if (
            state.get("task_id") != args.task
            or state.get("positive_uid") != args.positive_uid
            or state.get("checkpoint_sha256") != args.checkpoint_sha256
        ):
            raise ValueError("receipt belongs to another positive task")

    def save():
        write_receipt(args.receipt, state)

    if args.action == "inspect":
        checkpoint, grant = inspect(*load(), config, kms, ecr, args.positive_uid, args.checkpoint_sha256)
        print(
            json.dumps(
                {
                    "checkpoint_sha256": digest(checkpoint),
                    "original_grant_sha256": grant,
                    "action": "inspect_only",
                    "cluster_guid": CLUSTER_GUID,
                    "namespace": NAMESPACE,
                    "positive_uid": args.positive_uid,
                    "probe_sha256": hashlib.sha256(PROBE.read_bytes()).hexdigest(),
                    "planned_changes": [
                        "separate clone name/task label",
                        "negative callback guard",
                        "bounded deadline",
                        "new admitted policy; original KMS policy unchanged",
                    ],
                }
            )
        )
    elif args.action == "submit":
        credentials = session.get_credentials().get_frozen_credentials()
        env = {
            **os.environ,
            "AWS_ACCESS_KEY_ID": credentials.access_key,
            "AWS_SECRET_ACCESS_KEY": credentials.secret_key,
            "AWS_REGION": "us-west-2",
            "AWS_DEFAULT_REGION": "us-west-2",
        }
        env.pop("AWS_SESSION_TOKEN", None)
        if credentials.token:
            env["AWS_SESSION_TOKEN"] = credentials.token
        submit(
            load,
            config,
            kms,
            ecr,
            k8s,
            env,
            args.positive_uid,
            args.checkpoint_sha256,
            state,
            save,
            args.seconds,
        )
    elif args.action == "observe":
        result = observe_controlled(
            load, config, kms, ecr, k8s, args.positive_uid, args.checkpoint_sha256, state
        )
        state.setdefault("observations", []).append(result)
        save()
        print(json.dumps(result))
    else:
        cleanup(k8s, state)
        state.update(cleanup_requested_at=now())
        save()


if __name__ == "__main__":
    main()
