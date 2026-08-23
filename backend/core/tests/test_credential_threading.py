"""A capability may not claim credential-awareness it does not have (#1422).

``CREDENTIAL_AWARE_CAPABILITIES`` is a promise: operations on a cluster that
declares a role are allowed through for these capabilities and refused for the
rest. The promise is only as good as the drivers behind it, and nothing
connected the two -- adding a name to that set is a one-line edit that turns a
refusal into a silent wrong-account write, which is the exact failure mode the
whole credential module exists to avoid.

So the set is checked against driver source here. Two independent halves,
because each catches a way the other can be defeated:

* **Threading.** No module behind a listed capability may construct a cloud
  client directly. ``boto3.client`` bypasses ``aws_client`` and therefore the
  credential, whatever the config carries.
* **Carriage.** Every config a listed capability's funnel can build must have
  somewhere to put the credential. A driver reading ``config.credential`` on a
  config that lacks the field raises ``AttributeError`` at call time, which on
  these paths means during a provision.

Static, over the AST. These assertions are about which calls exist in the
source, and a runtime version would have to reach a real cloud to find out.
"""

from __future__ import annotations

import ast
import dataclasses
import pathlib

from core.cluster_credentials import CREDENTIAL_AWARE_CAPABILITIES

BACKEND = pathlib.Path(__file__).resolve().parents[2]
PROVIDERS = BACKEND / "providers"

# The AWS driver module behind each migrated capability. Only capabilities
# whose drivers were migrated in #1422 are listed; `log_query` and the
# `managed:*` family were migrated earlier and live under other trees.
MIGRATED_AWS_DRIVERS: dict[str, str] = {
    "cluster": "aws/cluster_eks.py",
    "registry": "aws/registry_ecr.py",
    "identity": "aws/identity_irsa.py",
    "secrets": "aws/secrets.py",
}

# Configs the three funnels stamp for AWS. Each must carry `credential`.
MIGRATED_AWS_CONFIGS: tuple[tuple[str, str], ...] = (
    ("aws.cluster_eks", "EKSConfig"),
    ("aws.registry_ecr", "ECRConfig"),
    ("aws.identity_irsa", "IRSAConfig"),
    ("aws.secrets", "SecretsConfig"),
)


def _direct_client_calls(path: pathlib.Path) -> list[str]:
    """`boto3.client(...)` / `boto3.resource(...)` / `boto3.Session(...)`
    call sites, which reach the ambient identity regardless of config."""
    tree = ast.parse(path.read_text())
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        value = node.func.value
        if isinstance(value, ast.Name) and value.id == "boto3":
            found.append(f"{path.name}:{node.lineno} boto3.{node.func.attr}(...)")
    return found


def test_every_migrated_capability_builds_clients_through_the_factory():
    offenders: list[str] = []
    for capability, rel in MIGRATED_AWS_DRIVERS.items():
        assert capability in CREDENTIAL_AWARE_CAPABILITIES, (
            f"{capability!r} has a migrated driver listed here but is missing from "
            "CREDENTIAL_AWARE_CAPABILITIES, so the operation is still refused."
        )
        path = PROVIDERS / rel
        assert path.exists(), f"{rel} moved; update MIGRATED_AWS_DRIVERS"
        offenders.extend(f"{capability}: {hit}" for hit in _direct_client_calls(path))

    assert not offenders, (
        "these construct a boto3 client directly, so they authenticate as the "
        "control plane whatever the cluster declared:\n  "
        + "\n  ".join(offenders)
        + "\n\nUse aws_client(service, region=..., credential=config.credential). "
        "A credential of None reproduces the ambient behaviour exactly, so the "
        "swap is behaviour-preserving."
    )


def test_every_migrated_config_can_carry_a_credential():
    import importlib

    missing: list[str] = []
    for module_name, cls_name in MIGRATED_AWS_CONFIGS:
        cls = getattr(importlib.import_module(module_name), cls_name)
        fields = {f.name for f in dataclasses.fields(cls)}
        if "credential" not in fields:
            missing.append(f"{module_name}.{cls_name}")

    assert not missing, (
        "these configs are stamped by a funnel but have nowhere to put the "
        "credential, so stamp_credential returns them untouched and the driver "
        "silently stays ambient:\n  " + "\n  ".join(missing)
    )


def test_the_direct_call_detector_still_detects():
    """A matcher that stops matching passes over an empty set."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        planted = pathlib.Path(tmp) / "planted.py"
        planted.write_text('import boto3\nx = boto3.client("s3", region_name="us-east-1")\n')
        assert _direct_client_calls(planted) == ["planted.py:2 boto3.client(...)"]

        clean = pathlib.Path(tmp) / "clean.py"
        clean.write_text('x = aws_client("s3", region="us-east-1", credential=None)\n')
        assert _direct_client_calls(clean) == []
