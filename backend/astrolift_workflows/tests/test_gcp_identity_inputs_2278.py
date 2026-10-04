"""Logical plan integrity without importing Django or constructing SDK clients."""

import copy
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from astrolift_workflows.gcp_identity_inputs import (
    AcceptedPreparationTemplate,
    EndpointGrant,
    LogicalSubject,
    accepted_preparation_template_from_payload,
)


def template():
    return AcceptedPreparationTemplate(
        (
            EndpointGrant(
                "projects/test-project/roles/predict", "projects/123/locations/us-central1/endpoints/42"
            ),
        ),
        (LogicalSubject(("01988c1f-1058-7f4c-b1ae-a7e014e69b21",), "app", "runtime"),),
        "a" * 64,
    )


def test_round_trip_and_source_snapshot_are_bound():
    original = template()
    assert accepted_preparation_template_from_payload(original.payload) == original
    assert replace(original, source_snapshot_sha256="b" * 64).sha256 != original.sha256
    assert replace(original, permissions=()).sha256 != original.sha256
    assert (
        replace(original, subjects=(replace(original.subjects[0], name="new-runtime"),)).sha256
        != original.sha256
    )


def test_sorted_union_hash_is_order_independent_without_inventing_uids():
    original = template()
    second = EndpointGrant(original.permissions[0].role, "projects/123/locations/us-central1/endpoints/43")
    subject = LogicalSubject(("01988c1f-1058-7f4c-b1ae-a7e014e69b22",), "app", "other")
    first = replace(
        original, permissions=(*original.permissions, second), subjects=(*original.subjects, subject)
    )
    reversed_plan = replace(
        first, permissions=tuple(reversed(first.permissions)), subjects=tuple(reversed(first.subjects))
    )
    assert first.sha256 == reversed_plan.sha256
    assert first.payload == reversed_plan.payload
    assert all(set(row) == {"environment_ids", "namespace", "name"} for row in first.payload["subjects"])


@pytest.mark.parametrize(
    "mutation",
    [
        lambda row: row.update(schema_version=True),
        lambda row: row.update(domain="unrelated"),
        lambda row: row.update(source_snapshot_sha256="opaque"),
        lambda row: row.update(sha256="a" * 64),
        lambda row: row["permissions"][0].update(role="roles/aiplatform.user"),
        lambda row: row["permissions"][0].update(resource="projects/123"),
        lambda row: row["subjects"][0].update(environment_ids=["00000000-0000-0000-0000-000000000000"]),
        lambda row: row["subjects"][0].update(name="../other"),
        lambda row: row["subjects"][0].update(uid="invented"),
        lambda row: row["subjects"].append(copy.deepcopy(row["subjects"][0])),
        lambda row: row["permissions"].append(copy.deepcopy(row["permissions"][0])),
        lambda row: row.update(permissions=[row["permissions"][0]] * 65),
        lambda row: row.update(subjects=[]),
    ],
)
def test_unrelated_opaque_or_ambiguous_payload_refused(mutation):
    payload = template().payload
    mutation(payload)
    with pytest.raises(ValueError, match="^INVALID_ACCEPTED_PREPARATION_TEMPLATE$"):
        accepted_preparation_template_from_payload(payload)


_IMPORT_GUARD = """
import builtins
import importlib
import importlib.util
import sys
import types

forbidden_roots = {'django', 'google', 'gcp', 'aws', 'azure', 'k8s_native',
                   '_sdk', 'boto3', 'botocore', 'kubernetes'}

def forbidden(name):
    return name.split('.', 1)[0] in forbidden_roots

class ForbiddenDependency(types.ModuleType):
    def __getattribute__(self, name):
        if name in {'__name__', '__spec__', '__loader__', '__package__', '__path__'}:
            return super().__getattribute__(name)
        dependency = super().__getattribute__('__name__')
        raise AssertionError('Forbidden dependency access: ' + dependency + '.' + name)

# Installed namespace .pth files may load google.cloud before this probe runs.
# Poison those modules as well as blocking imports, so startup is not authority
# to use a preloaded dependency or construct one of its clients.
before = set(sys.modules)
for name in tuple(before):
    if forbidden(name):
        sys.modules[name] = ForbiddenDependency(name)

original_import = builtins.__import__
original_import_module = importlib.import_module

def guarded_import(name, *args, **kwargs):
    if forbidden(name):
        raise AssertionError('Forbidden dependency import: ' + name)
    return original_import(name, *args, **kwargs)

def guarded_import_module(name, package=None):
    absolute = importlib.util.resolve_name(name, package) if name.startswith('.') else name
    if forbidden(absolute):
        raise AssertionError('Forbidden dependency import: ' + absolute)
    return original_import_module(name, package)

builtins.__import__ = guarded_import
importlib.import_module = guarded_import_module
"""


def _import_probe(code, *, preload=""):
    root = Path(__file__).resolve().parents[2]
    return subprocess.run(
        [
            sys.executable,
            "-c",
            preload + _IMPORT_GUARD + code + "\n"
            "added = sorted(name for name in set(sys.modules) - before if forbidden(name))\n"
            "assert not added, 'Forbidden dependencies loaded by target: ' + repr(added)\n",
        ],
        env={**os.environ, "PYTHONPATH": str(root)},
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )


def test_import_is_pure_in_fresh_interpreter():
    result = _import_probe(
        "\nfrom astrolift_workflows.gcp_identity_inputs import AcceptedPreparationTemplate\n"
        "assert AcceptedPreparationTemplate.__module__ == 'astrolift_workflows.gcp_identity_inputs'"
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "code",
    [
        "\nimport google.cloud",
        "\nfrom google.cloud import Client; Client()",
        "\nimportlib.import_module('google.cloud')",
        "\nbuiltins.__import__('google.cloud')",
        "\nsys.modules['google.cloud'].Client()",
    ],
)
def test_purity_guard_refuses_preloaded_dependency_import_or_client_access(code):
    result = _import_probe(
        code,
        preload="import sys, types\nsys.modules['google.cloud'] = types.ModuleType('google.cloud')\n",
    )
    assert result.returncode != 0
    assert "Forbidden dependency" in result.stderr


def test_shared_physical_subject_retains_all_aliases_and_detach_changes_plan():
    original = template()
    first = original.subjects[0]
    other = "01988c1f-1058-7f4c-b1ae-a7e014e69b22"
    shared = replace(original, subjects=(replace(first, environment_ids=(*first.environment_ids, other)),))
    assert len(shared.subjects) == 1
    assert shared.payload["subjects"][0]["environment_ids"] == [*first.environment_ids, other]
    assert accepted_preparation_template_from_payload(shared.payload) == shared
    assert shared.sha256 != original.sha256
    # A logical alias cannot acquire a second physical association.
    with pytest.raises(ValueError, match="INVALID_ACCEPTED_PREPARATION_TEMPLATE"):
        replace(shared, subjects=(*shared.subjects, LogicalSubject((other,), "app", "other")))
    with pytest.raises(ValueError, match="INVALID_ACCEPTED_PREPARATION_TEMPLATE"):
        replace(shared.subjects[0], environment_ids=(other, *first.environment_ids))
