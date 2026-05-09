"""Tests for app migration policy (#67)."""

from __future__ import annotations

import pytest

from astrolift_workflows.app_migration import (
    BUNDLE_VERSION,
    CLOCK_SKEW_SECONDS,
    JWT_TTL_SECONDS,
    BundleJwtClaims,
    DeploymentHistoryEntry,
    ExportBundle,
    ImportTargetContext,
    ManagedServiceVariant,
    MigrationError,
    SourceState,
    VerificationResult,
    build_claims,
    is_jwt_recently_issued,
    missing_secret_refs,
    plan_import,
    plan_pause_for_export,
    required_service_kinds,
    verify_claims,
)


def _bundle(**overrides) -> ExportBundle:
    base = dict(
        bundle_version=BUNDLE_VERSION,
        source_instance="install-a.platform.example",
        source_org_slug="acme",
        source_app_slug="api",
        manifest_normalized="[app]\nname='api'",
        env_config={"NODE_ENV": "production"},
        secret_refs=("DATABASE_URL", "STRIPE_KEY"),
        managed_services=(
            ManagedServiceVariant(
                kind="postgres", major_version=15, role="primary_db",
            ),
            ManagedServiceVariant(
                kind="redis", major_version=7, role="cache",
            ),
        ),
        deployment_history=(
            DeploymentHistoryEntry(
                image_digest="sha256:" + "a" * 64,
                manifest_sha256="b" * 64,
                deployed_at_unix=1_700_000_000,
            ),
        ),
        exported_at_unix=1_700_000_100,
    )
    base.update(overrides)
    return ExportBundle(**base)


# ---- bundle construction -------------------------------------------


def test_bundle_construction_defaults():
    b = _bundle()
    assert b.bundle_version == BUNDLE_VERSION
    assert len(b.deployment_history) == 1


def test_bundle_rejects_unsupported_version():
    with pytest.raises(MigrationError, match="version"):
        _bundle(bundle_version=99)


def test_bundle_requires_source_identifiers():
    with pytest.raises(MigrationError):
        _bundle(source_org_slug="")
    with pytest.raises(MigrationError):
        _bundle(source_app_slug="")
    with pytest.raises(MigrationError, match="source_instance"):
        _bundle(source_instance="")


def test_bundle_requires_manifest():
    with pytest.raises(MigrationError, match="manifest"):
        _bundle(manifest_normalized="")


def test_bundle_requires_deployment_history():
    """Empty history = nothing to first-deploy on B; refuse."""
    with pytest.raises(MigrationError, match="deployment_history"):
        _bundle(deployment_history=())


# ---- claims construction -------------------------------------------


def test_build_claims_basic():
    claims = build_claims(
        source_instance="install-a.platform.example",
        destination_instance="install-b.platform.example",
        bundle=_bundle(),
        issued_at_unix=1_700_000_100,
        jti="bundle-1",
    )
    assert claims.iss == "install-a.platform.example"
    assert claims.aud == "install-b.platform.example"
    assert claims.exp == 1_700_000_100 + JWT_TTL_SECONDS
    assert claims.jti == "bundle-1"


def test_build_claims_requires_destination():
    """Audience binding prevents bundle reuse against any install."""
    with pytest.raises(MigrationError, match="destination_instance"):
        build_claims(
            source_instance="a", destination_instance="",
            bundle=_bundle(), issued_at_unix=0, jti="j",
        )


def test_build_claims_rejects_same_destination():
    with pytest.raises(MigrationError, match="differ from source"):
        build_claims(
            source_instance="install-a", destination_instance="install-a",
            bundle=_bundle(), issued_at_unix=0, jti="j",
        )


def test_build_claims_rejects_oversized_ttl():
    """Operator can't ask for a longer-lived bundle than the
    security ceiling — even with explicit intent."""
    with pytest.raises(MigrationError, match="ttl_seconds"):
        build_claims(
            source_instance="a", destination_instance="b",
            bundle=_bundle(), issued_at_unix=0, jti="j",
            ttl_seconds=JWT_TTL_SECONDS + 1,
        )


def test_build_claims_rejects_zero_ttl():
    with pytest.raises(MigrationError, match="ttl_seconds"):
        build_claims(
            source_instance="a", destination_instance="b",
            bundle=_bundle(), issued_at_unix=0, jti="j",
            ttl_seconds=0,
        )


def test_build_claims_requires_jti():
    with pytest.raises(MigrationError, match="jti"):
        build_claims(
            source_instance="a", destination_instance="b",
            bundle=_bundle(), issued_at_unix=0, jti="",
        )


# ---- verification --------------------------------------------------


def _claims(**overrides) -> BundleJwtClaims:
    base = dict(
        source_instance="install-a.platform.example",
        destination_instance="install-b.platform.example",
        bundle=_bundle(),
        issued_at_unix=1_700_000_000,
        jti="bundle-1",
    )
    base.update(overrides)
    return build_claims(**base)


def test_verify_happy_path():
    decision = verify_claims(
        claims=_claims(),
        expected_destination="install-b.platform.example",
        now_unix=1_700_000_005,  # 5s after iat
        seen_jti_lookup=lambda jti: False,
    )
    assert decision.accepted is True
    assert decision.result == VerificationResult.OK


def test_verify_rejects_audience_mismatch():
    """Bundle aimed at install-b can't be played to install-c.
    Without aud binding, exfiltrated bundles would be reusable
    anywhere in the federation."""
    decision = verify_claims(
        claims=_claims(),
        expected_destination="install-c.platform.example",
        now_unix=1_700_000_005,
        seen_jti_lookup=lambda jti: False,
    )
    assert decision.accepted is False
    assert decision.result == VerificationResult.AUDIENCE_MISMATCH


def test_verify_rejects_expired():
    decision = verify_claims(
        claims=_claims(),
        expected_destination="install-b.platform.example",
        now_unix=1_700_000_000 + JWT_TTL_SECONDS + 60,  # well past exp
        seen_jti_lookup=lambda jti: False,
    )
    assert decision.result == VerificationResult.EXPIRED


def test_verify_tolerates_clock_skew_at_exp():
    """Federation across installs = some clock skew. Tolerate
    CLOCK_SKEW_SECONDS past exp."""
    now = 1_700_000_000 + JWT_TTL_SECONDS + (CLOCK_SKEW_SECONDS - 1)
    decision = verify_claims(
        claims=_claims(),
        expected_destination="install-b.platform.example",
        now_unix=now,
        seen_jti_lookup=lambda jti: False,
    )
    assert decision.accepted is True


def test_verify_rejects_not_yet_valid():
    """Iat in the future + skew exceeded."""
    decision = verify_claims(
        claims=_claims(issued_at_unix=1_700_000_100),
        expected_destination="install-b.platform.example",
        now_unix=1_700_000_100 - CLOCK_SKEW_SECONDS - 5,
        seen_jti_lookup=lambda jti: False,
    )
    assert decision.result == VerificationResult.NOT_YET_VALID


def test_verify_rejects_replay():
    """Single-use marker. Once jti recorded, bundle can't fire
    again — defends against snooped bundles."""
    decision = verify_claims(
        claims=_claims(),
        expected_destination="install-b.platform.example",
        now_unix=1_700_000_005,
        seen_jti_lookup=lambda jti: jti == "bundle-1",
    )
    assert decision.accepted is False
    assert decision.result == VerificationResult.ALREADY_USED


def test_verify_rejects_self_target():
    """Operator pointed export at source instance — no-op."""
    claims = build_claims(
        source_instance="install-a", destination_instance="install-b",
        bundle=_bundle(), issued_at_unix=0, jti="j",
    )
    decision = verify_claims(
        claims=claims,
        expected_destination="install-a",  # mismatch -> aud_mismatch first
        now_unix=10,
        seen_jti_lookup=lambda jti: False,
    )
    # Audience-mismatch fires before same-instance because aud
    # is the first cross-instance check.
    assert decision.result == VerificationResult.AUDIENCE_MISMATCH


def test_verify_same_instance_when_aud_matches_iss():
    """Build a (degenerate) claim where iss == aud, then verify
    against that aud. Triggers the SAME_INSTANCE path."""
    # Bypass build_claims via direct construction — build_claims
    # explicitly refuses same-instance.
    claims = BundleJwtClaims(
        iss="install-a", aud="install-a",
        iat=10, nbf=10, exp=10 + JWT_TTL_SECONDS, jti="j",
        bundle=_bundle(),
    )
    decision = verify_claims(
        claims=claims,
        expected_destination="install-a",
        now_unix=20,
        seen_jti_lookup=lambda jti: False,
    )
    assert decision.result == VerificationResult.SAME_INSTANCE


# ---- pause planning ------------------------------------------------


def test_pause_default_is_pause_source():
    plan = plan_pause_for_export(
        operator_requested_pause=True,
        current_source_state=SourceState.LIVE,
    )
    assert plan.pause_source is True
    assert plan.target_state == SourceState.PAUSED
    assert plan.revert_state == SourceState.LIVE


def test_pause_no_pause_keeps_live():
    """--no-pause: parallel-running A/B mode."""
    plan = plan_pause_for_export(
        operator_requested_pause=False,
        current_source_state=SourceState.LIVE,
    )
    assert plan.pause_source is False
    assert plan.target_state == SourceState.EXPORTED_LIVE


def test_pause_already_paused_does_not_re_pause():
    """If operator already paused via mutation before invoking
    export, export piggybacks; revert doesn't un-pause behind
    the operator's back."""
    plan = plan_pause_for_export(
        operator_requested_pause=True,
        current_source_state=SourceState.PAUSED,
    )
    assert plan.pause_source is False
    assert plan.target_state == SourceState.PAUSED
    assert plan.revert_state == SourceState.PAUSED


# ---- import plan ---------------------------------------------------


def test_plan_import_creates_org_when_missing():
    plan = plan_import(
        bundle=_bundle(),
        target=ImportTargetContext(
            destination_org_slug="acme",
            destination_project_slug="default",
            org_exists=False,
            project_exists=False,
        ),
    )
    assert plan.needs_org_create is True
    # Project create folds into org create when both missing
    assert plan.needs_project_create is False


def test_plan_import_creates_project_when_only_project_missing():
    plan = plan_import(
        bundle=_bundle(),
        target=ImportTargetContext(
            destination_org_slug="acme",
            destination_project_slug="newproject",
            org_exists=True,
            project_exists=False,
        ),
    )
    assert plan.needs_org_create is False
    assert plan.needs_project_create is True


def test_plan_import_lineage_stamp():
    """Spec: imported app's ``imported_from`` is
    ``<source_instance>/<src_org>/<src_app>``."""
    plan = plan_import(
        bundle=_bundle(),
        target=ImportTargetContext(
            destination_org_slug="acme",
            destination_project_slug="default",
            org_exists=True, project_exists=True,
        ),
    )
    assert plan.imported_from == (
        "install-a.platform.example/acme/api"
    )


def test_plan_import_picks_most_recent_history_entry():
    """Order in deployment_history isn't guaranteed; resolver
    picks latest by deployed_at_unix."""
    bundle = _bundle(deployment_history=(
        DeploymentHistoryEntry(
            image_digest="sha256:" + "1" * 64,
            manifest_sha256="x" * 64, deployed_at_unix=1_000,
        ),
        DeploymentHistoryEntry(
            image_digest="sha256:" + "2" * 64,
            manifest_sha256="y" * 64, deployed_at_unix=3_000,
        ),
        DeploymentHistoryEntry(
            image_digest="sha256:" + "3" * 64,
            manifest_sha256="z" * 64, deployed_at_unix=2_000,
        ),
    ))
    plan = plan_import(
        bundle=bundle,
        target=ImportTargetContext(
            destination_org_slug="acme",
            destination_project_slug="default",
            org_exists=True, project_exists=True,
        ),
    )
    assert plan.first_deploy_image_digest == "sha256:" + "2" * 64


def test_plan_import_requires_target_slugs():
    with pytest.raises(MigrationError):
        plan_import(
            bundle=_bundle(),
            target=ImportTargetContext(
                destination_org_slug="",
                destination_project_slug="default",
                org_exists=True, project_exists=True,
            ),
        )
    with pytest.raises(MigrationError):
        plan_import(
            bundle=_bundle(),
            target=ImportTargetContext(
                destination_org_slug="acme",
                destination_project_slug="",
                org_exists=True, project_exists=True,
            ),
        )


# ---- mapping helpers -----------------------------------------------


def test_required_service_kinds_dedupes():
    """Same kind+version twice (e.g. two postgres bindings of the
    same major) → one requirement; B picks one variant."""
    bundle = _bundle(managed_services=(
        ManagedServiceVariant(kind="postgres", major_version=15, role="db1"),
        ManagedServiceVariant(kind="postgres", major_version=15, role="db2"),
        ManagedServiceVariant(kind="redis", major_version=7, role="cache"),
    ))
    out = required_service_kinds(bundle=bundle)
    assert out == (("postgres", 15), ("redis", 7))


def test_missing_secret_refs():
    bundle = _bundle(secret_refs=("DATABASE_URL", "STRIPE_KEY"))
    missing = missing_secret_refs(
        bundle=bundle,
        secrets_present_on_destination=("DATABASE_URL",),
    )
    assert missing == ("STRIPE_KEY",)


def test_missing_secret_refs_all_present():
    bundle = _bundle(secret_refs=("X",))
    assert missing_secret_refs(
        bundle=bundle, secrets_present_on_destination=("X", "Y"),
    ) == ()


# ---- freshness helper ----------------------------------------------


def test_is_jwt_recently_issued_true_within_ttl():
    claims = _claims(issued_at_unix=1_000)
    assert is_jwt_recently_issued(claims=claims, now_unix=1_005) is True


def test_is_jwt_recently_issued_false_after_exp():
    claims = _claims(issued_at_unix=1_000)
    way_after = 1_000 + JWT_TTL_SECONDS + 60
    assert is_jwt_recently_issued(claims=claims, now_unix=way_after) is False
