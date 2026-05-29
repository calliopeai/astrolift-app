"""Tests for the variant-migration recipe catalog (#76)."""

from __future__ import annotations

from _sdk.variant_migration import (
    APP_TRAFFIC_PAUSED,
    EXTENSIONS_ON_TARGET,
    RECIPES,
    MigrationCatalog,
    MigrationRecipe,
    MigrationRequirement,
    variants_with_migration_path,
)


def test_rds_to_aurora_recipe_exists() -> None:
    recipe = RECIPES.find(
        kind="postgres", source="rds", target="aurora",
    )
    assert recipe is not None
    assert recipe.mode == "cutover"
    assert recipe.expected_downtime_seconds > 0
    assert recipe.rollback_supported is True


def test_rds_to_aurora_requires_extensions_check() -> None:
    """Aurora's allowed-extensions set differs from RDS — recipe
    must include the extension-compat pre-flight."""
    recipe = RECIPES.find(
        kind="postgres", source="rds", target="aurora",
    )
    assert recipe is not None
    codes = {r.code for r in recipe.pre_flight}
    assert "extensions_on_target" in codes


def test_blue_green_zero_downtime() -> None:
    recipe = RECIPES.find(
        kind="object_store", source="s3", target="gcs",
    )
    assert recipe is not None
    assert recipe.mode == "blue_green"
    assert recipe.expected_downtime_seconds == 0


def test_options_for_lists_all_targets() -> None:
    options = RECIPES.options_for(
        kind="postgres", source="rds",
    )
    targets = {r.target_variant for r in options}
    assert "aurora" in targets
    assert "cnpg" in targets


def test_variants_with_migration_path_helper() -> None:
    paths = variants_with_migration_path(
        kind="postgres", source="rds",
    )
    assert "aurora" in paths
    assert "cnpg" in paths
    assert paths == sorted(paths)


def test_unknown_recipe_returns_none() -> None:
    assert RECIPES.find(
        kind="postgres", source="never", target="elsewhere",
    ) is None


def test_redis_recipe_acknowledges_no_rollback() -> None:
    recipe = RECIPES.find(
        kind="redis", source="elasticache", target="operator",
    )
    assert recipe is not None
    assert recipe.rollback_supported is False
    # Notes explain why
    assert any("rebuild" in note for note in recipe.notes)


def test_custom_catalog_extension() -> None:
    """Operators can ship custom recipes (e.g., regulated tenant
    with a specific cross-cloud move)."""
    custom = MigrationCatalog(recipes=(
        MigrationRecipe(
            kind="postgres",
            source_variant="cloudsql",
            target_variant="alloydb",
            mode="in_place_upgrade",
            expected_downtime_seconds=60,
            rollback_supported=True,
            pre_flight=(APP_TRAFFIC_PAUSED,),
        ),
    ))
    recipe = custom.find(
        kind="postgres", source="cloudsql", target="alloydb",
    )
    assert recipe is not None
    assert recipe.mode == "in_place_upgrade"


def test_pre_flight_requirements_are_typed() -> None:
    """Requirements have stable codes — operator UIs can match
    them to render hints / runbooks."""
    assert APP_TRAFFIC_PAUSED.code == "app_traffic_paused"
    assert EXTENSIONS_ON_TARGET.code == "extensions_on_target"
    custom_req = MigrationRequirement(
        code="custom_check", description="x",
    )
    assert custom_req.code == "custom_check"
