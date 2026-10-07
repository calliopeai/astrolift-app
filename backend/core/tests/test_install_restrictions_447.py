"""What the install withholds and the model it serves (calliope-installer#447, #446)."""

from types import SimpleNamespace

import pytest

from astrolift_services.managed_service_catalog import CatalogResolutionError, list_catalog, resolve_variant
from astrolift_workflows.activities.managed_service_lifecycle import (
    ManagedServicePreflightError,
    _assert_not_withheld,
)
from core import install_restrictions as ir
from core.schema.types.install_policy import InstallPolicyQuery

ALL = "dns,databases,load_balancers,clusters"
MODEL_ENV = (
    "ASTROLIFT_PLATFORM_MODEL_PROVIDER",
    "ASTROLIFT_PLATFORM_MODEL_URL",
    "ASTROLIFT_PLATFORM_MODEL_ID",
    "ASTROLIFT_PLATFORM_MODEL_REPLICAS",
    "ANTHROPIC_API_KEY",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(ir.ENV_VAR, raising=False)
    for name in MODEL_ENV:
        monkeypatch.delenv(name, raising=False)


def _shared_model(monkeypatch, replicas: str | None = "2"):
    monkeypatch.setenv("ASTROLIFT_PLATFORM_MODEL_PROVIDER", "openai_compatible")
    monkeypatch.setenv("ASTROLIFT_PLATFORM_MODEL_URL", "http://internal-acme-al-model.elb.amazonaws.com/v1")
    monkeypatch.setenv("ASTROLIFT_PLATFORM_MODEL_ID", "Qwen/Qwen2.5-7B-Instruct")
    if replicas is not None:
        monkeypatch.setenv("ASTROLIFT_PLATFORM_MODEL_REPLICAS", replicas)


def _info(authenticated=True):
    user = SimpleNamespace(is_authenticated=authenticated)
    return SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=user)))


# ---- reading the installer's list -------------------------------------------


def test_unset_withholds_nothing():
    assert ir.withheld() == frozenset()
    assert ir.reason("databases") == ""
    assert ir.database_refusal("postgres", "rds") == ""
    assert ir.controller_refusal("external-dns") == ""
    assert ir.app_ingress_refusal(SimpleNamespace(ingress_class="alb")) == ""


def test_reads_the_installer_list_and_ignores_unknown_names(monkeypatch):
    monkeypatch.setenv(ir.ENV_VAR, " dns , clusters,,storage")
    assert ir.withheld() == {"dns", "clusters"}
    monkeypatch.setenv(ir.ENV_VAR, ALL)
    assert ir.withheld() == set(ir.CAPABILITIES)


def test_databases_refuse_rds_backed_variants_only(monkeypatch):
    monkeypatch.setenv(ir.ENV_VAR, "databases")
    for kind, variant in [
        ("postgres", "rds"),
        ("postgres", "aurora_postgres"),
        ("mysql", "aurora_mysql"),
        ("mssql", "rds_sqlserver_web"),
        ("database_proxy", "rds_proxy"),
        ("document_db", "documentdb"),
        ("graph_db", "neptune"),
    ]:
        assert "Databases are withheld" in ir.database_refusal(kind, variant)
    assert ir.database_refusal("postgres", "cnpg") == ""
    assert ir.database_refusal("mysql", "operator") == ""
    assert ir.database_refusal("redis", "elasticache") == ""


def test_app_ingress_is_refused_only_where_it_would_make_a_load_balancer(monkeypatch):
    monkeypatch.setenv(ir.ENV_VAR, "load_balancers")
    assert "Load balancers are withheld" in ir.app_ingress_refusal(SimpleNamespace(ingress_class="alb"))
    assert ir.app_ingress_refusal(SimpleNamespace(ingress_class="envoy")) == ""
    assert ir.app_ingress_refusal(SimpleNamespace(ingress_class="nginx")) == ""
    monkeypatch.setenv(ir.ENV_VAR, "dns")
    assert ir.app_ingress_refusal(SimpleNamespace(ingress_class="alb")) == ""


# ---- databases: the catalogue and the activity -------------------------------


def _aws_postgres():
    return {row.variant: row for row in list_catalog("aws") if row.kind == "postgres"}


def test_catalogue_offers_withheld_databases_disabled_with_the_reason(monkeypatch):
    assert _aws_postgres()["rds"].available
    assert resolve_variant(plugin_slug="aws", kind="postgres", requested_variant=None).variant == "rds"

    monkeypatch.setenv(ir.ENV_VAR, "databases")
    rows = _aws_postgres()
    assert not rows["rds"].available
    assert "Databases are withheld" in rows["rds"].unavailable_reason
    assert not rows["rds"].is_default_for_kind
    assert rows["cnpg"].available


def test_resolution_refuses_a_withheld_database_with_the_reason(monkeypatch):
    monkeypatch.setenv(ir.ENV_VAR, "databases")
    with pytest.raises(CatalogResolutionError, match="Databases are withheld"):
        resolve_variant(plugin_slug="aws", kind="postgres", requested_variant="aurora_postgres")
    with pytest.raises(CatalogResolutionError, match="Databases are withheld.*cnpg"):
        resolve_variant(plugin_slug="aws", kind="postgres", requested_variant=None)
    assert resolve_variant(plugin_slug="aws", kind="postgres", requested_variant="cnpg").variant == "cnpg"


def test_activity_refuses_an_rds_backed_service_without_retrying(monkeypatch):
    svc = SimpleNamespace(kind="postgres", variant="rds")
    _assert_not_withheld(svc)
    monkeypatch.setenv(ir.ENV_VAR, "databases")
    with pytest.raises(ManagedServicePreflightError, match="Databases are withheld"):
        _assert_not_withheld(svc)
    _assert_not_withheld(SimpleNamespace(kind="postgres", variant="cnpg"))


# ---- the console's read -------------------------------------------------------


def test_withheld_capabilities_query(monkeypatch):
    query = InstallPolicyQuery()
    assert query.astrolift_withheld_capabilities(_info()) == []
    monkeypatch.setenv(ir.ENV_VAR, "clusters,dns")
    rows = query.astrolift_withheld_capabilities(_info())
    assert [r.capability for r in rows] == ["dns", "clusters"]
    assert all(r.reason for r in rows)
    with pytest.raises(PermissionError):
        query.astrolift_withheld_capabilities(_info(authenticated=False))


def test_install_managed_model_query(monkeypatch):
    query = InstallPolicyQuery()
    assert query.astrolift_install_managed_model(_info()) is None
    monkeypatch.setenv("ASTROLIFT_PLATFORM_MODEL_PROVIDER", "bedrock")
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    assert query.astrolift_install_managed_model(_info()) is None

    _shared_model(monkeypatch)
    shared = query.astrolift_install_managed_model(_info())
    assert (shared.model_id, shared.replicas) == ("Qwen/Qwen2.5-7B-Instruct", 2)
    assert not hasattr(shared, "endpoint")
    with pytest.raises(PermissionError):
        query.astrolift_install_managed_model(_info(authenticated=False))


def test_unreported_replicas_are_unknown_not_guessed(monkeypatch):
    from astrolift_agents.services.platform_model import install_managed_model

    _shared_model(monkeypatch, replicas=None)
    shared = install_managed_model()
    assert shared.replicas is None and shared.gpus == 0
    monkeypatch.setenv("ASTROLIFT_PLATFORM_MODEL_REPLICAS", "three")
    assert install_managed_model().replicas is None
    monkeypatch.setenv("ASTROLIFT_PLATFORM_MODEL_REPLICAS", "3")
    assert install_managed_model().gpus == 3
