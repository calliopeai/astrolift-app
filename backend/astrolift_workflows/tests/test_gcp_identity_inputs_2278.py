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


def test_import_is_pure_in_fresh_interpreter():
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from astrolift_workflows.gcp_identity_inputs import AcceptedPreparationTemplate; assert not any(name == 'django' or name.startswith(('django.', 'google.', 'gcp.')) for name in sys.modules)",
        ],
        env={**os.environ, "PYTHONPATH": str(root)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


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
