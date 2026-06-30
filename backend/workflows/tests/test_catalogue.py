"""Platform workflow catalogue seed (spec 40 §4 / §4.1).

Covers: every bundled TOML parses via the #973 serializer; the seed
creates the 8 null-org read-only definitions + stages with the right
roles / skills / patterns; the seed is idempotent (re-run = no dupes,
in-place updates); the new WorkflowStage columns (prompt / approvers /
fan_out tri-state) round-trip losslessly through the model (closes the
#973 gap).
"""

from __future__ import annotations

import pytest

from workflows.catalogue_seed import (
    CATALOGUE_DIR,
    _fan_out_columns,
    seed_workflow_catalogue,
)
from workflows.manifest import (
    definition_to_manifest,
    emit_workflow_manifest,
    parse_workflow_manifest,
)
from workflows.models import WorkflowDefinition, WorkflowStage

# The §4.1 locked v0.1 set: slug → (pattern_kind, stage_count).
EXPECTED = {
    "ooda": ("chained", 4),
    "rasd": ("review_loop", 4),
    "feature-dev": ("chained", 5),
    "bug-fix": ("chained", 5),
    "code-review": ("single", 1),
    "moderate": ("review_loop", 4),
    "research": ("fan_out", 4),
    "supervisor-worker": ("supervisor_worker", 3),
}


def _seed():
    return seed_workflow_catalogue(WorkflowDefinition, WorkflowStage)


def _clear_globals():
    """Drop the catalogue the 0006 data migration seeds at test-DB setup, so a
    test can exercise the create-from-empty path."""
    WorkflowStage.objects.filter(definition__organization__isnull=True).delete()
    WorkflowDefinition.objects.filter(organization__isnull=True).delete()


# --------------------------------------------------------------------------- #
# Bundled TOML parses (no DB)
# --------------------------------------------------------------------------- #


def test_catalogue_dir_has_eight_tomls():
    tomls = sorted(p.stem for p in CATALOGUE_DIR.glob("*.toml"))
    assert tomls == sorted(EXPECTED)


@pytest.mark.parametrize("slug", sorted(EXPECTED))
def test_each_toml_parses_and_matches_pattern(slug):
    pattern, stage_count = EXPECTED[slug]
    toml = (CATALOGUE_DIR / f"{slug}.toml").read_text(encoding="utf-8")
    parsed = parse_workflow_manifest(toml)
    assert parsed.definition.slug == slug
    assert parsed.definition.pattern == pattern
    assert len(parsed.stages) == stage_count
    # Catalogue stages are role-only — no concrete agent bound (§4.1).
    assert all(s.agent is None for s in parsed.stages)


def test_fan_out_columns_tri_state():
    assert _fan_out_columns(0) == (None, False)
    assert _fan_out_columns(3) == (3, False)
    assert _fan_out_columns("dynamic") == (None, True)


# --------------------------------------------------------------------------- #
# Seed (real Postgres)
# --------------------------------------------------------------------------- #


@pytest.mark.django_db
def test_seed_creates_eight_readonly_globals():
    _clear_globals()
    result = _seed()
    assert result.definitions_created == 8

    defs = WorkflowDefinition.objects.filter(organization__isnull=True)
    assert defs.count() == 8
    for slug, (pattern, stage_count) in EXPECTED.items():
        defn = defs.get(slug=slug)
        # Null-org → platform-global, read-only to tenants (§2.1).
        assert defn.organization_id is None
        assert defn.pattern_kind == pattern
        stages = defn.stages.order_by("order")
        assert stages.count() == stage_count
        assert [s.order for s in stages] == list(range(stage_count))
        # Globals omit a concrete agent; it's bound at configure-time (§2.2/§4).
        assert all(s.agent_definition_id is None for s in stages)


@pytest.mark.django_db
def test_seed_roles_and_skills():
    _seed()
    feature = WorkflowDefinition.objects.get(organization__isnull=True, slug="feature-dev")
    by_order = {s.order: s for s in feature.stages.all()}
    assert by_order[0].role == "specifier"
    assert by_order[2].skill_refs == ["write-tests"]
    assert by_order[4].skill_refs == ["commit-pr"]


@pytest.mark.django_db
def test_seed_gate_carries_approvers():
    _seed()
    rasd = WorkflowDefinition.objects.get(organization__isnull=True, slug="rasd")
    gate = rasd.stages.get(kind=WorkflowStage.StageKind.HUMAN_GATE)
    assert gate.prompt
    assert gate.approvers == ["team:reviewers"]
    assert gate.timeout_seconds == 86400


@pytest.mark.django_db
def test_seed_research_carries_fan_out():
    _seed()
    research = WorkflowDefinition.objects.get(organization__isnull=True, slug="research")
    searcher = research.stages.get(order=0)
    # "dynamic" fan-out: count NULL, dynamic flag set (§5.4 tri-state).
    assert searcher.fan_out_dynamic is True
    assert searcher.fan_out_count is None
    assert searcher.skill_refs == ["web-research"]

    sup = WorkflowDefinition.objects.get(organization__isnull=True, slug="supervisor-worker")
    worker = sup.stages.get(order=1)
    # static fan-out: count set, dynamic flag clear.
    assert worker.fan_out_count == 3
    assert worker.fan_out_dynamic is False


@pytest.mark.django_db
def test_seed_is_idempotent():
    _clear_globals()
    first = _seed()
    assert first.definitions_created == 8

    def_ids = set(WorkflowDefinition.objects.filter(organization__isnull=True).values_list("id", flat=True))
    stage_ids = set(
        WorkflowStage.objects.filter(definition__organization__isnull=True).values_list("id", flat=True)
    )

    second = _seed()
    assert second.definitions_created == 0
    assert second.definitions_updated == 8
    assert second.stages_pruned == 0

    # No dupes; rows updated in place (same PKs).
    assert WorkflowDefinition.objects.filter(organization__isnull=True).count() == 8
    assert (
        set(WorkflowDefinition.objects.filter(organization__isnull=True).values_list("id", flat=True))
        == def_ids
    )
    assert (
        set(WorkflowStage.objects.filter(definition__organization__isnull=True).values_list("id", flat=True))
        == stage_ids
    )


@pytest.mark.django_db
def test_seeded_definitions_round_trip_losslessly():
    """A model-backed export of every seeded definition re-parses to the
    same structured manifest — proving the new prompt/approvers/fan_out
    columns close the #973 lossy-export gap."""
    _seed()
    for slug in EXPECTED:
        defn = WorkflowDefinition.objects.get(organization__isnull=True, slug=slug)
        source = parse_workflow_manifest((CATALOGUE_DIR / f"{slug}.toml").read_text(encoding="utf-8"))
        exported = parse_workflow_manifest(emit_workflow_manifest(definition_to_manifest(defn)))

        assert exported.definition == source.definition
        assert exported.stages == source.stages
