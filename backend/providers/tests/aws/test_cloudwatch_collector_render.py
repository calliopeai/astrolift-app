"""Actual pinned offline render and refusal boundaries, without Docker or a cluster."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import platform
import stat
import sys
import tarfile
from dataclasses import replace
from pathlib import Path

import pytest

from aws._cloudwatch_collector import staged_component
from aws.cloudwatch_collector_render import CollectorRenderError, render_collector

ARTIFACTS = Path(os.environ.get("COLLECTOR_RENDER_ARTIFACTS", ""))
NATIVE_TARGET = f"{platform.system().lower()}-" + {"arm64": "arm64", "aarch64": "arm64", "x86_64": "amd64"}.get(
    platform.machine(), "unsupported"
)
GUID = "00000000-0000-4000-8000-000000000170"
ROLE = f"arn:aws:iam::123456789012:role/astrolift/astrolift-{GUID}-fluent-bit"
GROUP = f"/astrolift/clusters/{GUID}/pods"


@pytest.fixture
def component():
    return staged_component(irsa_role_arn=ROLE, region="us-west-2", log_group=GROUP)


@pytest.fixture
def archive():
    path = ARTIFACTS / "fluent-bit-0.58.2.tgz"
    if not os.environ.get("COLLECTOR_RENDER_ARTIFACTS") or not path.is_file():
        pytest.skip("Set COLLECTOR_RENDER_ARTIFACTS to independently verified native archives")
    return path.read_bytes()


def render(component, archive, **kwargs):
    return render_collector(
        component,
        archive=archive,
        namespace="astrolift-system",
        release_name="fluent-bit",
        helm_binary=str(ARTIFACTS / f"helm-v4.3.0-{NATIVE_TARGET}"),
        **kwargs,
    )


def test_actual_pinned_helm_renders_owned_profile_without_cluster_or_local_plugins(
    component, archive, tmp_path, monkeypatch
):
    # An inherited hostile plugin location must not influence offline rendering.
    monkeypatch.setenv("HELM_PLUGINS", str(tmp_path / "foreign-plugins"))
    monkeypatch.setenv("KUBECONFIG", str(tmp_path / "does-not-exist"))
    resources = render(component, archive)
    by_kind = {resource["kind"]: resource for resource in resources}
    assert len(by_kind) == len(resources)
    assert set(by_kind) == {"ServiceAccount", "ClusterRole", "ClusterRoleBinding", "ConfigMap", "DaemonSet", "Service"}
    assert by_kind["ServiceAccount"]["metadata"]["annotations"]["eks.amazonaws.com/role-arn"] == ROLE
    assert by_kind["ClusterRoleBinding"]["subjects"] == [
        {"kind": "ServiceAccount", "name": "fluent-bit", "namespace": "astrolift-system"}
    ]
    pod = by_kind["DaemonSet"]["spec"]["template"]["spec"]
    assert pod["nodeSelector"] == {"kubernetes.io/os": "linux"}
    assert pod["serviceAccountName"] == "fluent-bit"
    [container] = pod["containers"]
    assert container["image"] == "cr.fluentbit.io/fluent/fluent-bit:5.1.2"
    assert {entry["name"]: entry["value"] for entry in container["env"]} == {
        "AWS_REGION": "us-west-2",
        "LOG_GROUP_NAME": GROUP,
    }
    config = by_kind["ConfigMap"]["data"]["fluent-bit.conf"]
    assert "Merge_Log Off" in config and "K8S-Logging.Parser Off" in config and "K8S-Logging.Exclude Off" in config
    assert "log_key" not in config


def test_archive_change_refuses_before_any_executable(component, archive, monkeypatch):
    monkeypatch.setattr(
        "aws.cloudwatch_collector_render.subprocess.run", lambda *a, **k: pytest.fail("executed unreviewed bytes")
    )
    with pytest.raises(CollectorRenderError, match="checksum"):
        render(component, archive + b"changed")


@pytest.mark.parametrize("change", ["metadata", "region", "group", "account", "chart", "namespace", "release"])
def test_profile_or_target_change_refuses_before_renderer(component, archive, change, monkeypatch):
    monkeypatch.setattr(
        "aws.cloudwatch_collector_render.subprocess.run", lambda *a, **k: pytest.fail("executed changed source")
    )
    namespace, release = "astrolift-system", "fluent-bit"
    if change == "metadata":
        component.helm_values["config"]["filters"] = "[FILTER]\nName kubernetes\nMerge_Log On\n"
    elif change == "region":
        component.helm_values["cloudwatch"]["region"] = '{{ .Files.Get "secret" }}'
    elif change == "group":
        component.helm_values["cloudwatch"]["logGroup"] = "/foreign/group"
    elif change == "account":
        component.helm_values["serviceAccount"]["annotations"]["eks.amazonaws.com/role-arn"] = (
            "arn:aws:iam::1:role/foreign"
        )
    elif change == "chart":
        component = replace(component, chart_version="latest")
    elif change == "namespace":
        namespace = "foreign"
    else:
        release = "--enable-dns"
    with pytest.raises(CollectorRenderError):
        render_collector(component, archive=archive, namespace=namespace, release_name=release)


def test_missing_renderer_has_no_fallback_or_raw_os_error(component, archive, tmp_path):
    with pytest.raises(CollectorRenderError) as caught:
        render_collector(
            component,
            archive=archive,
            namespace="astrolift-system",
            release_name="fluent-bit",
            helm_binary=str(tmp_path / "PRIVATE_PATH_MARKER"),
        )
    assert "PRIVATE_PATH_MARKER" not in str(caught.value)
    assert caught.value.__suppress_context__


def test_failed_renderer_diagnostics_are_not_exposed(component, archive, tmp_path):
    binary = tmp_path / "fake-helm"
    binary.write_text(
        f"#!{sys.executable}\nimport sys\n"
        "if '--short' in sys.argv: print('v4.3.0'); sys.exit(0)\n"
        "print('PRIVATE_RENDERED_DATA_MARKER')\n"
        "sys.stderr.write('PRIVATE_RENDERED_DATA_MARKER')\n"
        "sys.exit(1)\n"
    )
    binary.chmod(0o700)
    with pytest.raises(CollectorRenderError) as caught:
        render_collector(
            component, archive=archive, namespace="astrolift-system", release_name="fluent-bit", helm_binary=str(binary)
        )
    assert "PRIVATE_RENDERED_DATA_MARKER" not in str(caught.value)


def installer():
    source = Path(__file__).resolve().parents[3] / "scripts" / "install_helm.py"
    spec = importlib.util.spec_from_file_location("collector_install_helm", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("target", ["linux-amd64", "linux-arm64"])
def test_actual_runtime_archive_installer_is_architecture_and_checksum_pinned(target, tmp_path):
    path = ARTIFACTS / f"helm-v4.3.0-{target}.tar.gz"
    if not path.is_file():
        pytest.skip("Set COLLECTOR_RENDER_ARTIFACTS to independently verified Linux archives")
    pin = json.loads(
        (Path(__file__).resolve().parents[2] / "aws" / "_cloudwatch_collector" / "helm-runtime.json").read_text()
    )
    destination = tmp_path / "helm"
    installer().install_archive(
        path.read_bytes(), target=target, digest=pin["archives"][target], destination=destination
    )
    binary = destination.read_bytes()
    assert binary[:4] == b"\x7fELF"
    assert int.from_bytes(binary[18:20], "little") == (62 if target == "linux-amd64" else 183)
    assert stat.S_IMODE(destination.stat().st_mode) == 0o755
    # Verify/build Linux artifacts here; execute the Darwin counterpart natively.


@pytest.mark.parametrize("change", ["digest", "symlink", "duplicate", "foreign-path"])
def test_installer_never_extracts_unverified_or_ambiguous_archive(change, tmp_path):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as bundle:
        name = "../escape" if change == "foreign-path" else "linux-amd64/helm"
        item = tarfile.TarInfo(name)
        item.size = 4
        if change == "symlink":
            item.type, item.linkname, item.size = tarfile.SYMTYPE, "/foreign", 0
        bundle.addfile(item, io.BytesIO(b"data"))
        if change == "duplicate":
            bundle.addfile(item, io.BytesIO(b"data"))
    archive = output.getvalue()
    digest = "0" * 64 if change == "digest" else hashlib.sha256(archive).hexdigest()
    destination = tmp_path / "helm"
    with pytest.raises(RuntimeError):
        installer().install_archive(archive, target="linux-amd64", digest=digest, destination=destination)
    assert not destination.exists()
