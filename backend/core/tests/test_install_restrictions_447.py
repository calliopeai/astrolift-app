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
AWS = SimpleNamespace(provider_plugin=SimpleNamespace(slug="aws"))
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
    assert ir.controller_refusal("external-dns", AWS) == ""
    assert ir.cluster_delete_refusal(AWS) == ""


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


def test_controllers_and_cluster_deletes_are_refused_only_on_aws(monkeypatch):
    """The Denies are IAM statements on the AWS task role. A GKE, AKS or
    on-prem cluster's external-dns, or deleting such a cluster, never calls AWS."""
    monkeypatch.setenv(ir.ENV_VAR, ALL)
    assert "DNS is withheld" in ir.controller_refusal("external-dns", AWS)
    assert "Load balancers are withheld" in ir.controller_refusal("aws-load-balancer-controller", AWS)
    assert ir.controller_refusal("cert-manager", AWS) == ""
    assert "Cluster lifecycle is withheld" in ir.cluster_delete_refusal(AWS)
    for slug in ("gcp", "azure", "k8s_native"):
        other = SimpleNamespace(provider_plugin=SimpleNamespace(slug=slug))
        assert ir.controller_refusal("external-dns", other) == ""
        assert ir.cluster_delete_refusal(other) == ""


class _Route53:
    def ensure_record(self, **kw):
        return "wrote"

    def list_zones(self):
        return ["zone"]


def test_dns_writes_are_refused_with_the_reason_and_reads_pass(monkeypatch):
    driver = _Route53()
    assert ir.guard_dns_driver(driver, "aws") is driver

    monkeypatch.setenv(ir.ENV_VAR, "dns")
    guarded = ir.guard_dns_driver(driver, "aws")
    assert guarded.list_zones() == ["zone"]
    for write in ("ensure_record", "delete_record", "provision_zone", "deprovision_zone"):
        with pytest.raises(ir.WithheldCapabilityError, match="DNS is withheld"):
            getattr(guarded, write)(zone="z", name="n", type="A", value="v")
    assert ir.guard_dns_driver(driver, "gcp") is driver


def test_platform_dns_drivers_carry_the_guard(monkeypatch):
    from astrolift_workflows.activities.static_site import _us_east_1_dns_driver

    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv(ir.ENV_VAR, "dns")
    with pytest.raises(ir.WithheldCapabilityError, match="DNS is withheld"):
        _us_east_1_dns_driver().ensure_record(zone="z", name="n", type="CNAME", value="v")


class _AwsCluster:
    slug = "prod"
    region = "us-west-2"
    provider_plugin = SimpleNamespace(slug="aws")
    provider_plugin_id = "aws"
    provider_config = {"region": "us-west-2", "base_domain": "mail.acme.example"}
    auth_config: dict = {}
    cloud_account_id = ""
    cloud_account_verified_at = None


@pytest.mark.django_db
def test_the_ses_driver_config_carries_the_dns_refusal(monkeypatch):
    """The SES driver writes its verification records into Route53 from
    inside the provider package, which cannot import this guard; the one
    config funnel every SES driver is built from carries the decision."""
    from core.cluster_observability import managed_config_for

    assert managed_config_for("aws", _AwsCluster(), kind="email").dns_withheld_reason == ""
    monkeypatch.setenv(ir.ENV_VAR, "dns")
    assert managed_config_for("aws", _AwsCluster(), kind="email").dns_withheld_reason.startswith(
        "DNS is withheld"
    )


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


@pytest.mark.parametrize("kind", ["mssql", "document_db", "graph_db"])
def test_withholding_does_not_promote_an_in_cluster_variant_to_default(monkeypatch, kind):
    """Kinds with no configured default needed an explicit variant before;
    withholding the RDS-backed ones must refuse with the reason, not quietly
    pick the lone in-cluster variant left."""
    with pytest.raises(CatalogResolutionError, match="explicit variant"):
        resolve_variant(plugin_slug="aws", kind=kind, requested_variant=None)
    monkeypatch.setenv(ir.ENV_VAR, "databases")
    assert not any(
        row.is_default_for_kind for row in list_catalog("aws", include_extended=True) if row.kind == kind
    )
    with pytest.raises(CatalogResolutionError, match="Databases are withheld"):
        resolve_variant(plugin_slug="aws", kind=kind, requested_variant=None)


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
