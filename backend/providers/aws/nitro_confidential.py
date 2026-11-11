"""Ciphertext-only confidential run image preparation and attested KMS grants."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import subprocess
import tempfile
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aws.nitro_confidential_image import add_run_layer, task_environment, verify_encrypted_manifest

if TYPE_CHECKING:
    from collections.abc import Callable

POLICY = "sandbox.amazonaws.com/policy"
PUBLIC_KEY = b"""-----BEGIN PUBLIC KEY-----
MHYwEAYHKoZIzj0CAQYFK4EEACIDYgAESqdVXO82n+Zh5+IFLh1xjmxZLkjjZWvf
DCS927SdVYjP0AZ92XCxS+ucrs9cjuNlTaDARTHov2XVfMVgau8TEPFKGDRmVZXJ
c25CVihKHLFK8T8hNMIL+fbGcvE2fwIO
-----END PUBLIC KEY-----
"""


@dataclass(frozen=True)
class ConfidentialConfig:
    registry: str
    key_admin_arn: str
    runtime_role_arn: str
    runtime_directory: Path
    service_account: str
    architecture: str = "amd64"
    skopeo: str = "/usr/bin/skopeo"
    temporary_root: str = "/dev/shm"
    resource_tags: dict[str, str] = field(default_factory=dict)

    def validate(self) -> None:
        if (
            not isinstance(self.resource_tags, dict)
            or len(self.resource_tags) > 43
            or any(
                not isinstance(name, str)
                or not isinstance(value, str)
                or not name
                or len(name) > 128
                or len(value) > 256
                or name.lower().startswith("aws:")
                or name in {"astrolift-task-id", "Name", "Service", "Owner", "Environment", "Region", "ManagedBy"}
                for name, value in self.resource_tags.items()
            )
        ):
            raise ValueError("confidential resource tags must preserve mandatory ownership fields")
        if not re.fullmatch(r"[0-9]{12}\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com/[a-z0-9][a-z0-9/_-]*", self.registry):
            raise ValueError("confidential run images require a dedicated private ECR repository")
        for arn in (self.key_admin_arn, self.runtime_role_arn):
            if not re.fullmatch(r"arn:aws:iam::[0-9]{12}:role/[A-Za-z0-9+=,.@_/-]+", arn):
                raise ValueError("confidential key administration and runtime require explicit IAM role ARNs")
            if arn.split(":")[4] != self.registry.split(".")[0]:
                raise ValueError("confidential KMS roles and run repository must belong to the same account")
        if self.key_admin_arn == self.runtime_role_arn:
            raise ValueError("the parent-visible runtime role cannot administer its attestation key")
        if self.architecture not in {"amd64", "arm64"} or not self.service_account:
            raise ValueError("confidential tasks require an explicit platform and Pod Identity service account")
        if not Path(self.skopeo).is_absolute() or not self.runtime_directory.is_absolute():
            raise ValueError("confidential runtime tools must have absolute configured paths")
        if not Path(self.temporary_root).is_dir():
            raise ValueError("private temporary image workspace is unavailable")


class ConfidentialToolError(ValueError):
    def __init__(self, tool: str, returncode: int, diagnostics: bytes) -> None:
        self.diagnostics = diagnostics
        super().__init__(f"{Path(tool).name} failed with exit {returncode}; private diagnostics are withheld")


def command(argv: list[str], *, env: dict[str, str], input_data: bytes | None = None) -> bytes:
    result = subprocess.run(argv, input=input_data, env=env, capture_output=True, timeout=900)
    if result.returncode:
        raise ConfidentialToolError(argv[0], result.returncode, result.stderr)
    return result.stdout


def verified_tools(directory: Path, temporary: Path, env: dict[str, str]) -> tuple[Path, Path]:
    key = temporary / "aws-runtime.pub"
    key.write_bytes(PUBLIC_KEY)
    command(
        [
            "openssl",
            "dgst",
            "-sha384",
            "-verify",
            str(key),
            "-signature",
            str(directory / "SHA256SUM.sig"),
            str(directory / "SHA256SUM"),
        ],
        env=env,
    )
    checked = {}
    for line in (directory / "SHA256SUM").read_text().splitlines():
        match = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?([A-Za-z0-9._-]+)", line)
        if match is None or match[2] in checked:
            raise ValueError("invalid signed AWS runtime checksums")
        checked[match[2]] = match[1].lower()
    tools = []
    for name in ("eks-nitro-sandbox-genpolicy", "nitro-sandbox-keyprovider"):
        data = (directory / name).read_bytes()
        if checked.get(name) != hashlib.sha256(data).hexdigest():
            raise ValueError("AWS runtime binary differs from its signed checksum")
        target = temporary / name
        target.write_bytes(data)
        target.chmod(0o700)
        tools.append(target)
    return tools[0], tools[1]


def key_policy(config: ConfidentialConfig, pcr17: str = "") -> dict[str, Any]:
    config.validate()
    statements = [
        {
            "Sid": "TrustedControlPlaneAdministration",
            "Effect": "Allow",
            "Principal": {"AWS": config.key_admin_arn},
            "Action": [
                "kms:DescribeKey",
                "kms:GetKeyPolicy",
                "kms:PutKeyPolicy",
                "kms:Encrypt",
                "kms:ScheduleKeyDeletion",
                "kms:ListResourceTags",
            ],
            "Resource": "*",
        }
    ]
    if pcr17:
        if not re.fullmatch(r"[0-9a-f]{96}", pcr17):
            raise ValueError("predicted PCR17 must be the signed generator's SHA384 measurement")
        statements.append(
            {
                "Sid": "AttestedRunImageDecryption",
                "Effect": "Allow",
                "Principal": {"AWS": config.runtime_role_arn},
                "Action": ["kms:Decrypt"],
                "Resource": "*",
                "Condition": {
                    "StringEqualsIgnoreCase": {
                        "kms:RecipientAttestation:PCR17": pcr17,
                        "kms:RecipientAttestation:PCR8:ServiceName": "eks.amazonaws.com",
                    }
                },
            }
        )
    return {"Version": "2012-10-17", "Statement": statements}


def policy_measurement(output: bytes) -> str:
    report = output.decode()
    matches = re.findall(r"^--- Predicted PCR17 ---\r?\n([0-9a-f]{96})\r?$", report, re.MULTILINE)
    marker = "--- KMS Key Policy Condition ---"
    if len(matches) != 1 or report.count(marker) != 1:
        raise ValueError("signed generator did not emit a unique PCR17 report")
    condition = report.split(marker, 1)[1].strip()
    if not condition.startswith('"Condition":'):
        raise ValueError("signed generator omitted KMS attestation conditions")
    observed = json.loads(condition[len('"Condition":') :])
    if observed != {
        "StringEqualsIgnoreCase": {
            "kms:RecipientAttestation:PCR17": matches[0],
            "kms:RecipientAttestation:PCR8:ServiceName": "eks.amazonaws.com",
        }
    }:
        raise ValueError("signed generator did not bind PCR17 and EKS identity")
    return str(matches[0])


def clean_pod(pod: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(pod)
    result.pop("status", None)
    for key in ("uid", "resourceVersion", "creationTimestamp", "managedFields", "generation"):
        result["metadata"].pop(key, None)
    result["metadata"].setdefault("annotations", {}).pop(POLICY, None)
    return result


def prepared_pod_hash(pod: dict[str, Any]) -> str:
    from copy import deepcopy

    result = deepcopy(pod)
    policy = result.get("metadata", {}).get("annotations", {}).get(POLICY)
    result = clean_pod(result)
    if policy is not None:
        result["metadata"]["annotations"][POLICY] = policy
    result.get("spec", {}).pop("nodeName", None)
    # PodTopologyLabels adds these two scheduling facts at binding time,
    # after the final admitted Pod and policy have already been prepared.
    labels = result["metadata"].get("labels")
    if isinstance(labels, dict):
        for name in ("topology.kubernetes.io/region", "topology.kubernetes.io/zone"):
            labels.pop(name, None)
        if not labels:
            result["metadata"].pop("labels", None)
    return hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()


def validate_admitted(pod: dict[str, Any], expected: dict[str, Any], uid: int, gid: int) -> None:
    spec = pod.get("spec", {})
    original = expected["spec"]
    if (
        pod.get("kind") != "Pod"
        or pod.get("metadata", {}).get("name") != expected["metadata"]["name"]
        or pod.get("metadata", {}).get("namespace") != expected["metadata"]["namespace"]
        or spec.get("runtimeClassName") != "confidential-sandbox"
        or spec.get("serviceAccountName") != original["serviceAccountName"]
        or len(spec.get("containers", [])) != 1
        or spec.get("initContainers")
        or spec.get("hostNetwork")
        or spec.get("hostPID")
        or spec.get("hostIPC")
        or spec.get("hostUsers") is False
        or spec.get("shareProcessNamespace")
        or spec.get("securityContext", {}).get("fsGroup") != original.get("securityContext", {}).get("fsGroup")
        or (spec.get("nodeSelector") or {}).get("kubernetes.io/arch") != original["nodeSelector"]["kubernetes.io/arch"]
        or spec.get("enableServiceLinks") is not False
    ):
        raise ValueError("confidential Pod admission changed a protected run boundary")
    container = spec["containers"][0]
    security = container.get("securityContext", {})
    pod_security = spec.get("securityContext", {})
    if (
        container.get("image") != original["containers"][0]["image"]
        or container.get("command")
        or container.get("args")
        or container.get("envFrom")
        or security.get("runAsUser", pod_security.get("runAsUser")) != uid
        or security.get("runAsGroup", pod_security.get("runAsGroup")) != gid
        or security.get("allowPrivilegeEscalation") is not False
        or security.get("privileged")
        or security.get("capabilities", {}).get("add")
        or security.get("capabilities", {}).get("drop") != ["ALL"]
        or pod_security.get("runAsNonRoot") is not True
        or container.get("stdin")
        or container.get("tty")
        or container.get("volumeDevices")
    ):
        raise ValueError("confidential Pod admission changed the encrypted bootstrap or ownership")
    expected_env = original["containers"][0]["env"]
    allowed = {
        "AWS_CONTAINER_CREDENTIALS_FULL_URI",
        "AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE",
        "AWS_REGION",
        "AWS_DEFAULT_REGION",
        "AWS_STS_REGIONAL_ENDPOINTS",
    }
    env = container.get("env", [])
    if not isinstance(env, list) or any(
        not isinstance(item, dict) or not isinstance(item.get("name"), str) for item in env
    ):
        raise ValueError("confidential admission returned malformed environment entries")
    names = [item["name"] for item in env]
    if len(names) != len(set(names)):
        raise ValueError("confidential admission returned duplicate environment names")
    if any(
        set(item) != {"name", "value"} or not isinstance(item["value"], str) for item in env if item["name"] in allowed
    ):
        raise ValueError("confidential identity environment must contain only literal values")
    # Revalidation also accepts an expected Pod that already carries the exact
    # identity projection, as required when regenerating its final policy.
    if [item for item in env if item["name"] not in allowed] != [
        item for item in expected_env if item.get("name") not in allowed
    ]:
        raise ValueError("confidential admission injected non-identity environment variables")
    native = {item["name"]: item.get("value") for item in env if item.get("name") in allowed}
    token_root = "/var/run/secrets/pods.eks.amazonaws.com/serviceaccount"
    if (
        native.get("AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE") != token_root + "/eks-pod-identity-token"
        or native.get("AWS_CONTAINER_CREDENTIALS_FULL_URI")
        not in {
            "http://169.254.170.23/v1/credentials",
            "http://[fd00:ec2::23]/v1/credentials",
        }
        or ("AWS_STS_REGIONAL_ENDPOINTS" in native and native["AWS_STS_REGIONAL_ENDPOINTS"] != "regional")
        or any(
            native[name] != original["containers"][0]["image"].split(".")[3]
            for name in ("AWS_REGION", "AWS_DEFAULT_REGION")
            if name in native
        )
    ):
        raise ValueError("confidential Pod requires the standard EKS Pod Identity credential projection")
    # Only EKS Pod Identity's projected token can be delivered by the parent; run secrets are image layers.
    volumes = spec.get("volumes", [])
    if len(volumes) != 1:
        raise ValueError("confidential Pod requires exactly its EKS identity projected volume")
    for volume in volumes:
        if volume.get("name") != "eks-pod-identity-token" or set(volume) != {"name", "projected"}:
            raise ValueError("confidential admission injected unsupported host-delivered storage")
        sources = volume["projected"].get("sources", [])
        if len(sources) != 1 or set(sources[0]) != {"serviceAccountToken"}:
            raise ValueError("confidential identity projection must contain only its service account token")
        token = sources[0]["serviceAccountToken"]
        if token.get("audience") != "pods.eks.amazonaws.com" or token.get("path") != "eks-pod-identity-token":
            raise ValueError("confidential identity token audience or path is unexpected")
    mounts = container.get("volumeMounts", [])
    if len(mounts) != 1:
        raise ValueError("confidential Pod requires exactly its EKS identity token mount")
    for mount in mounts:
        if (
            mount.get("name") != "eks-pod-identity-token"
            or mount.get("readOnly") is not True
            or mount.get("mountPath", "").rstrip("/") != token_root
            or mount.get("subPath")
            or mount.get("subPathExpr")
        ):
            raise ValueError("confidential admission injected an unsupported mount")


def prepare_run(
    *,
    config: ConfidentialConfig,
    pod: dict[str, Any],
    secret: dict[str, Any] | None,
    task_id: str,
    callback_hash: str,
    kms: Any,
    ecr: Any,
    env: dict[str, str],
    admit: Callable[[dict[str, Any]], dict[str, Any]],
    save: Callable[[dict[str, Any]], None],
) -> tuple[dict[str, Any], dict[str, Any]]:
    config.validate()
    if not re.fullmatch(r"[0-9a-f]{32}", task_id):
        raise ValueError("confidential run requires the full task UUID")
    spec = pod["spec"]
    container = spec["containers"][0]
    base_image = container["image"]
    if not re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", base_image):
        raise ValueError("confidential task base image must be pinned by immutable digest")
    values = task_environment(spec, secret)
    uid, gid = spec.get("securityContext", {}).get("runAsUser"), spec.get("securityContext", {}).get("runAsGroup")
    if type(uid) is not int or type(gid) is not int or uid < 1 or gid < 1:
        raise ValueError("confidential tasks require explicit non-root numeric Pod uid/gid")
    spec["serviceAccountName"] = config.service_account
    spec["enableServiceLinks"] = False
    node_selector = dict(spec.get("nodeSelector") or {})
    if node_selector.get("kubernetes.io/arch", config.architecture) != config.architecture:
        raise ValueError("confidential task architecture conflicts with its node selector")
    spec["nodeSelector"] = {**node_selector, "kubernetes.io/arch": config.architecture}
    checkpoint: dict[str, Any] = {
        "task_id": task_id,
        "callback_hash": callback_hash,
        "registry": config.registry,
        "attestation_verified": False,
    }
    with (
        tempfile.TemporaryDirectory(prefix="astro-confidential-", dir=config.temporary_root) as work,
        tempfile.TemporaryDirectory(prefix="astro-public-tools-", dir="/tmp") as public_tools,
    ):
        root = Path(work)
        # The private tmpfs may be noexec. Only the verified public binaries and
        # their public verification key belong in the executable snapshot.
        # Credentials, image layers and subprocess scratch remain in the tmpfs.
        env = {**env, "TMPDIR": str(root), "TMP": str(root), "TEMP": str(root)}
        generator, keyprovider = verified_tools(config.runtime_directory, Path(public_tools), env)
        skopeo = [config.skopeo, "--tmpdir", str(root)]
        auth = root / "registry-auth.json"
        for token in ecr.get_authorization_token()["authorizationData"]:
            password = base64.b64decode(token["authorizationToken"]).split(b":", 1)[1]
            command(
                [
                    *skopeo,
                    "login",
                    "--authfile",
                    str(auth),
                    "--username",
                    "AWS",
                    "--password-stdin",
                    token["proxyEndpoint"],
                ],
                env=env,
                input_data=password,
            )
        # The signed AWS generator reads HOME/.docker/config.json directly.
        # Give only that subprocess an ephemeral private home; never modify
        # the controller's home or rely solely on ignored auth-path overrides.
        private_home = root / "registry-home"
        private_home.mkdir(mode=0o700)
        docker_auth = private_home / ".docker"
        docker_auth.mkdir(mode=0o700)
        docker_config = docker_auth / "config.json"
        docker_config.write_bytes(auth.read_bytes())
        docker_config.chmod(0o600)
        generator_env = {
            **env,
            "HOME": str(private_home),
            "DOCKER_CONFIG": str(docker_auth),
            "REGISTRY_AUTH_FILE": str(auth),
        }
        layout = root / "source"
        command(
            [
                *skopeo,
                "--override-os",
                "linux",
                "--override-arch",
                config.architecture,
                "copy",
                "--authfile",
                str(auth),
                "--format",
                "oci",
                "docker://" + base_image,
                "oci:" + str(layout) + ":run",
            ],
            env=env,
        )
        add_run_layer(
            layout,
            task_id=task_id,
            environment=values,
            command=container.get("command"),
            args=container.get("args"),
            uid=uid,
            gid=gid,
        )
        key = kms.create_key(
            Description="Astrolift confidential task image",
            KeyUsage="ENCRYPT_DECRYPT",
            KeySpec="SYMMETRIC_DEFAULT",
            Policy=json.dumps(key_policy(config)),
            Tags=[
                {"TagKey": name, "TagValue": value}
                for name, value in {
                    **config.resource_tags,
                    "astrolift-task-id": task_id,
                    "Name": "astrolift-confidential-" + task_id,
                    "Service": "confidential-tasks",
                    "Owner": "astrolift",
                    "Environment": "task",
                    "Region": config.registry.split(".")[3],
                    "ManagedBy": "astrolift",
                }.items()
            ],
        )
        checkpoint["key_arn"] = key["KeyMetadata"]["Arn"]
        expected_account, expected_region = config.registry.split(".")[0], config.registry.split(".")[3]
        if not re.fullmatch(
            r"arn:aws:kms:" + re.escape(expected_region) + ":" + expected_account + r":key/[0-9a-f-]+",
            checkpoint["key_arn"],
        ):
            kms.schedule_key_deletion(KeyId=checkpoint["key_arn"], PendingWindowInDays=7)
            raise ValueError("created attestation key does not belong to the configured region/account")
        try:
            save(checkpoint)
        except BaseException:
            kms.schedule_key_deletion(KeyId=checkpoint["key_arn"], PendingWindowInDays=7)
            raise
        try:
            provider_config = root / "ocicrypt.json"
            provider_config.write_text(
                json.dumps({"key-providers": {"awskms": {"cmd": {"path": str(keyprovider), "args": []}}}})
            )
            encrypted_env = {**env, "OCICRYPT_KEYPROVIDER_CONFIG": str(provider_config)}
            digest_file = root / "encrypted-digest"
            image = config.registry + ":" + task_id
            command(
                [
                    *skopeo,
                    "copy",
                    "--authfile",
                    str(auth),
                    "--encryption-key",
                    "provider:awskms:" + checkpoint["key_arn"],
                    "--digestfile",
                    str(digest_file),
                    "oci:" + str(layout) + ":run",
                    "docker://" + image,
                ],
                env=encrypted_env,
            )
            digest = digest_file.read_text().strip()
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
                raise ValueError("registry did not return an immutable encrypted image digest")
            checkpoint["image"] = config.registry + "@" + digest
            save(checkpoint)
            raw = command(
                [*skopeo, "inspect", "--authfile", str(auth), "--raw", "docker://" + checkpoint["image"]], env=env
            )
            if "sha256:" + hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError("encrypted registry manifest does not match its recorded digest")
            verify_encrypted_manifest(json.loads(raw))
            container.update(
                image=checkpoint["image"], env=[{"name": "ASTROLIFT_CONFIDENTIAL_TASK_ID", "value": task_id}]
            )
            container.pop("command", None)
            container.pop("args", None)
            first = clean_pod(admit(pod))
            validate_admitted(first, pod, uid, gid)
            manifest, encoded = root / "pod.json", root / "policy.b64"
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
                    str(encoded),
                    str(manifest),
                ],
                env=generator_env,
            )
            checkpoint["pcr17"] = policy_measurement(report)
            policy = encoded.read_text().strip()
            if not base64.b64decode(policy, validate=True):
                raise ValueError("signed generator produced an empty policy")
            first["metadata"]["annotations"][POLICY] = policy
            second = admit(first)
            if clean_pod(second) != clean_pod(first) or second["metadata"].get("annotations", {}).get(POLICY) != policy:
                raise ValueError("confidential final policy does not survive admission unchanged")
            kms.put_key_policy(
                KeyId=checkpoint["key_arn"],
                PolicyName="default",
                Policy=json.dumps(key_policy(config, checkpoint["pcr17"])),
            )
            checkpoint["policy"] = policy
            checkpoint["pod_sha256"] = prepared_pod_hash(first)
            save(checkpoint)
            return first, checkpoint
        except BaseException:
            try:
                cleanup_run(checkpoint, kms=kms, ecr=ecr)
            except Exception:
                checkpoint["cleanup_pending"] = True
                with suppress(Exception):
                    save(checkpoint)
            raise


def cleanup_run(checkpoint: dict[str, Any], *, kms: Any, ecr: Any) -> None:
    task_id = checkpoint.get("task_id", "")
    key_arn = checkpoint.get("key_arn", "")
    registry = checkpoint.get("registry", "")
    if not re.fullmatch(r"[0-9a-f]{32}", task_id) or not re.fullmatch(
        r"arn:aws:kms:[a-z0-9-]+:[0-9]{12}:key/[0-9a-f-]+",
        key_arn,
    ):
        raise ValueError("confidential cleanup has no verifiable task/key identity")
    match = re.fullmatch(r"([0-9]{12})\.dkr\.ecr\.([a-z0-9-]+)\.amazonaws\.com/(.+)", registry)
    if not match or match[1] != key_arn.split(":")[4] or match[2] != key_arn.split(":")[3]:
        raise ValueError("confidential key and image cleanup identity differ")
    tags = kms.list_resource_tags(KeyId=key_arn)
    if (
        tags.get("Truncated")
        or {row["TagKey"]: row["TagValue"] for row in tags["Tags"]}.get("astrolift-task-id") != task_id
    ):
        raise ValueError("refusing to retire a KMS key owned by another task")
    if kms.describe_key(KeyId=key_arn)["KeyMetadata"]["KeyState"] != "PendingDeletion":
        kms.schedule_key_deletion(KeyId=key_arn, PendingWindowInDays=7)
    try:
        images = ecr.describe_images(registryId=match[1], repositoryName=match[3], imageIds=[{"imageTag": task_id}])[
            "imageDetails"
        ]
    except ecr.exceptions.ImageNotFoundException:
        return
    if len(images) != 1 or images[0].get("imageTags") != [task_id]:
        raise ValueError("refusing to delete a run image with another owner's retention tags")
    digest = images[0]["imageDigest"]
    expected = checkpoint.get("image")
    if expected and expected != registry + "@" + digest:
        raise ValueError("confidential run image tag changed before cleanup")
    result = ecr.batch_delete_image(registryId=match[1], repositoryName=match[3], imageIds=[{"imageDigest": digest}])
    if result.get("failures"):
        raise ValueError("confidential run image cleanup failed")
