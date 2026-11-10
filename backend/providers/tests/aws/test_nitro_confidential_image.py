"""Run credentials are confined to private layers, never registry-visible image config."""

import gzip
import io
import json
import os
import subprocess
import sys
import tarfile

import pytest

from aws.nitro_confidential_image import (
    BOOTSTRAP,
    BOOTSTRAP_PATH,
    BUNDLE_PATH,
    ConfidentialImageError,
    _blob,
    _write_blob,
    add_run_layer,
    task_environment,
    verify_encrypted_manifest,
)


@pytest.fixture
def layout(tmp_path):
    (tmp_path / "blobs/sha256").mkdir(parents=True)
    config = {
        "architecture": "amd64",
        "os": "linux",
        "rootfs": {"type": "layers", "diff_ids": []},
        "config": {
            "Entrypoint": ["python3", "runner.py"],
            "Cmd": ["--task"],
            "User": "root",
            "Env": ["BASE_SETTING=value"],
            "Labels": {"private-build-info": "remove", "ai.astrolift.agent-image.contract": "7"},
        },
    }
    descriptor = _write_blob(tmp_path, json.dumps(config).encode(), "application/vnd.oci.image.config.v1+json")
    manifest = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": descriptor,
        "layers": [],
    }
    descriptor = _write_blob(tmp_path, json.dumps(manifest).encode(), "application/vnd.oci.image.manifest.v1+json")
    (tmp_path / "index.json").write_text(json.dumps({"schemaVersion": 2, "manifests": [descriptor]}))
    return tmp_path


def test_run_bundle_is_only_in_private_layer_with_nonroot_bootstrap(layout):
    secret = "synthetic-callback-credential"
    task_id = "a" * 32
    add_run_layer(layout, task_id=task_id, environment={"ASTROLIFT_CLUSTER_KEY": secret})
    index = json.loads((layout / "index.json").read_bytes())
    assert index["manifests"][0]["annotations"]["org.opencontainers.image.ref.name"] == "run"
    manifest = json.loads(_blob(layout, index["manifests"][0]))
    config_bytes = _blob(layout, manifest["config"])
    assert secret.encode() not in config_bytes
    config = json.loads(config_bytes)
    assert config["config"]["User"] == "42042:42042"
    assert config["config"]["Entrypoint"] == ["python3", "/" + BOOTSTRAP_PATH]
    assert config["config"]["Cmd"] == []
    with tarfile.open(fileobj=io.BytesIO(gzip.decompress(_blob(layout, manifest["layers"][-1])))) as archive:
        bundle = json.loads(archive.extractfile(BUNDLE_PATH).read())
        assert bundle == {
            "task_id": task_id,
            "environment": {"ASTROLIFT_CLUSTER_KEY": secret, "BASE_SETTING": "value"},
            "argv": ["python3", "runner.py", "--task"],
        }
        assert archive.getmember(BUNDLE_PATH).mode == 0o400
        assert archive.getmember(BUNDLE_PATH).uid == 42042


def test_credential_callbacks_require_tls(layout):
    with pytest.raises(ConfidentialImageError, match="HTTPS"):
        add_run_layer(layout, task_id="a" * 32, environment={"AGENT_CALLBACK_URL": "http://api/task"})


def test_corrupt_base_blob_cannot_be_repackaged(layout):
    index = json.loads((layout / "index.json").read_text())
    digest = index["manifests"][0]["digest"].split(":")[1]
    (layout / "blobs/sha256" / digest).write_bytes(b"tampered")
    with pytest.raises(ConfidentialImageError, match="integrity"):
        add_run_layer(layout, task_id="a" * 32, environment={})


def test_only_resolved_task_secrets_are_packaged():
    pod = {
        "containers": [
            {
                "env": [
                    {"name": "LITERAL", "value": "value"},
                    {"name": "TOKEN", "valueFrom": {"secretKeyRef": {"name": "run", "key": "token"}}},
                ]
            }
        ]
    }
    secret = {"metadata": {"name": "run"}, "stringData": {"token": "synthetic-secret"}}
    assert task_environment(pod, secret) == {"LITERAL": "value", "TOKEN": "synthetic-secret"}
    pod["containers"][0]["env"][1]["valueFrom"]["secretKeyRef"]["name"] = "unresolved-external"
    with pytest.raises(ConfidentialImageError, match="unresolved"):
        task_environment(pod, secret)


@pytest.mark.parametrize(
    "media_type,annotations",
    [
        ("application/vnd.oci.image.layer.v1.tar+gzip", {}),
        ("application/vnd.oci.image.layer.v1.tar+gzip+encrypted", {}),
    ],
)
def test_unencrypted_or_wrong_keyprovider_layer_is_refused(media_type, annotations):
    with pytest.raises(ConfidentialImageError, match="every run image layer"):
        verify_encrypted_manifest({"layers": [{"mediaType": media_type, "annotations": annotations}]})


def test_all_layers_must_use_kms_encryption():
    encrypted = {
        "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip+encrypted",
        "annotations": {"org.opencontainers.image.enc.keys.provider.awskms": "d3JhcHBlZC1rZXk="},
    }
    verify_encrypted_manifest({"layers": [encrypted, encrypted]})
    with pytest.raises(ConfidentialImageError):
        verify_encrypted_manifest({"layers": [encrypted, {"mediaType": "plaintext"}]})


@pytest.mark.parametrize("matching", [True, False])
def test_guest_bootstrap_restores_private_environment_before_exec_and_refuses_wrong_task(tmp_path, matching):
    bundle = tmp_path / "run.json"
    bundle.write_text(
        json.dumps(
            {
                "task_id": "a" * 32,
                "environment": {"PRIVATE_TOKEN": "synthetic-secret"},
                "argv": [
                    sys.executable,
                    "-c",
                    "import os; assert os.environ['PRIVATE_TOKEN']=='synthetic-secret'; print('bootstrap-exec-ok')",
                ],
            }
        )
    )
    script = BOOTSTRAP.decode().replace("/opt/astrolift-confidential/run.json", str(bundle))
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        timeout=10,
        env={**os.environ, "ASTROLIFT_CONFIDENTIAL_TASK_ID": ("a" if matching else "b") * 32},
    )
    assert result.returncode == (0 if matching else 125)
    assert result.stdout == (b"bootstrap-exec-ok\n" if matching else b"")
    assert b"synthetic-secret" not in result.stderr


@pytest.mark.parametrize("packet", ["not-base64!", "d3JhcHBlZA==,", ",d3JhcHBlZA==", "===="])
def test_encrypted_manifest_rejects_malformed_provider_packets(packet):
    with pytest.raises(ConfidentialImageError, match="provider packets"):
        verify_encrypted_manifest(
            {
                "layers": [
                    {
                        "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip+encrypted",
                        "annotations": {"org.opencontainers.image.enc.keys.provider.awskms": packet},
                    }
                ]
            }
        )


def test_encrypted_manifest_accepts_multiple_opaque_provider_packets():
    verify_encrypted_manifest(
        {
            "layers": [
                {
                    "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip+encrypted",
                    "annotations": {"org.opencontainers.image.enc.keys.provider.awskms": "d3JhcHBlZA==,b3BhcXVl"},
                }
            ]
        }
    )
