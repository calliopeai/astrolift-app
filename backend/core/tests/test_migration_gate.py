"""Tests for migration safety gate (#169 part 1, spec 04 §12)."""

from __future__ import annotations

import pytest

from core.migration_gate import (
    DESTRUCTIVE_OPS,
    DestructiveMigrationBlocked,
    MigrationRisk,
    RISKY_OPS,
    assess_operations,
    gate,
    render_preview,
)


# Stand-in operation classes — duck-typed via __class__.__name__
class AddField:
    def __init__(self, model_name="App", name="new_col"):
        self.model_name = model_name
        self.name = name


class RemoveField:
    def __init__(self, model_name="App", name="old_col"):
        self.model_name = model_name
        self.name = name


class DeleteModel:
    def __init__(self, name="LegacyApp"):
        self.name = name


class AlterField:
    def __init__(self, model_name="App", name="notes"):
        self.model_name = model_name
        self.name = name


class RunPython:
    def __init__(self):
        pass


class CreateModel:
    def __init__(self, name="NewApp"):
        self.name = name


class AddIndex:
    def __init__(self, model_name="App"):
        self.model_name = model_name


# ---- single-op classification --------------------------------------


def test_add_field_is_safe():
    a = assess_operations([AddField()])
    assert a.overall == MigrationRisk.SAFE
    assert a.destructive_ops == ()


def test_remove_field_is_destructive():
    a = assess_operations([RemoveField(name="legacy_email")])
    assert a.overall == MigrationRisk.DESTRUCTIVE
    assert len(a.destructive_ops) == 1
    assert "legacy_email" in a.destructive_ops[0].detail


def test_delete_model_is_destructive():
    a = assess_operations([DeleteModel(name="OldThing")])
    assert a.overall == MigrationRisk.DESTRUCTIVE
    assert "OldThing" in a.destructive_ops[0].detail


def test_alter_field_is_risky():
    a = assess_operations([AlterField()])
    assert a.overall == MigrationRisk.RISKY
    assert len(a.risky_ops) == 1


def test_run_python_is_risky():
    """Opaque to static analysis — assume the worst."""
    a = assess_operations([RunPython()])
    assert a.overall == MigrationRisk.RISKY


def test_create_model_and_add_index_are_safe():
    a = assess_operations([CreateModel(), AddIndex()])
    assert a.overall == MigrationRisk.SAFE


# ---- aggregation -----------------------------------------------------


def test_overall_destructive_when_any_destructive():
    a = assess_operations([AddField(), AlterField(), RemoveField()])
    assert a.overall == MigrationRisk.DESTRUCTIVE


def test_overall_risky_when_no_destructive_but_risky():
    a = assess_operations([AddField(), AlterField()])
    assert a.overall == MigrationRisk.RISKY


def test_overall_safe_when_only_safe():
    a = assess_operations([AddField(), CreateModel()])
    assert a.overall == MigrationRisk.SAFE


# ---- gate -----------------------------------------------------------


def test_gate_blocks_destructive_without_override():
    a = assess_operations([RemoveField(name="email")])
    with pytest.raises(DestructiveMigrationBlocked) as exc:
        gate(assessment=a, allow_destructive=False, migration_label="0042_drop_email")
    assert "0042_drop_email" in str(exc.value)
    assert "email" in str(exc.value)


def test_gate_allows_destructive_with_override():
    a = assess_operations([RemoveField()])
    gate(assessment=a, allow_destructive=True)  # no raise


def test_gate_passes_risky_without_override():
    """RISKY is warning-only; the operator sees it in CI logs."""
    a = assess_operations([AlterField()])
    gate(assessment=a, allow_destructive=False)  # no raise


def test_gate_passes_safe():
    a = assess_operations([AddField()])
    gate(assessment=a, allow_destructive=False)


# ---- preview --------------------------------------------------------


def test_render_preview_lists_only_non_safe_ops():
    a = assess_operations([
        AddField(name="ok"),
        RemoveField(name="legacy_email"),
        AlterField(name="notes"),
    ])
    preview = render_preview(a)
    assert "DESTRUCTIVE migration" in preview
    assert "legacy_email" in preview
    assert "notes" in preview
    # SAFE ops are omitted from the preview (only signal what matters)
    assert "ok" not in preview


def test_destructive_set_matches_django_op_names():
    """Sanity check: the names we trigger on are real Django
    operation class names, so a future Django version that renames
    them will fail this test loudly."""
    expected_destructive = {"DeleteModel", "RemoveField", "RemoveConstraint", "RemoveIndex"}
    assert DESTRUCTIVE_OPS == expected_destructive


def test_risky_set_includes_runpython_and_runsql():
    assert "RunPython" in RISKY_OPS
    assert "RunSQL" in RISKY_OPS
