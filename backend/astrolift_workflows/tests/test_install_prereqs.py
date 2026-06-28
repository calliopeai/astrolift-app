"""Unit tests for the pure helpers in ``install_prereqs.py`` (#66).

Activity bodies need Django + a TenantCluster row + plugin loader,
so they live in integration tests. The pure helpers — value merging
and the synthetic bindings-secret name — are isolated here and run
without any platform context.
"""

from __future__ import annotations

# ---- _merge_helm_values --------------------------------------------------


def test_merge_helm_values_empty_overrides_returns_base():
    from astrolift_workflows.activities.install_prereqs import _merge_helm_values

    base = {"replicas": 3, "image": {"tag": "v1.2"}}
    assert _merge_helm_values(base, {}) == base


def test_merge_helm_values_override_replaces_top_level_key():
    """Operator pick of ``mode=acm`` for the tls_issuer component
    replaces the driver's default ``mode`` key in helm_values."""
    from astrolift_workflows.activities.install_prereqs import _merge_helm_values

    base = {"mode": "acme_letsencrypt_prod", "email": "ops@x.com"}
    out = _merge_helm_values(base, {"mode": "acm"})
    assert out == {"mode": "acm", "email": "ops@x.com"}


def test_merge_helm_values_unknown_override_passes_through():
    """A driver can add a new option without the merge helper
    changing — overrides flow through verbatim."""
    from astrolift_workflows.activities.install_prereqs import _merge_helm_values

    base = {"existing": "v"}
    out = _merge_helm_values(base, {"new_option": "x"})
    assert out == {"existing": "v", "new_option": "x"}


def test_merge_helm_values_overrides_does_not_mutate_base():
    """Pure function semantic — the driver's helm_values must be
    safe to reuse across calls; the merger must not aliase."""
    from astrolift_workflows.activities.install_prereqs import _merge_helm_values

    base = {"a": 1}
    out = _merge_helm_values(base, {"a": 2})
    assert base == {"a": 1}
    assert out == {"a": 2}


def test_merge_helm_values_handles_none_overrides():
    """The mutation layer may pass ``None`` rather than ``{}`` for a
    component with no overrides."""
    from astrolift_workflows.activities.install_prereqs import _merge_helm_values

    base = {"k": "v"}
    assert _merge_helm_values(base, None) == base  # type: ignore[arg-type]


# ---- _bindings_secret_name ---------------------------------------------


def test_bindings_secret_name_format():
    from astrolift_workflows.activities.app_lifecycle import _bindings_secret_name

    assert _bindings_secret_name("api") == "astrolift-bindings-api"


def test_bindings_secret_name_with_dashes():
    from astrolift_workflows.activities.app_lifecycle import _bindings_secret_name

    assert _bindings_secret_name("my-web-app") == "astrolift-bindings-my-web-app"


def test_bindings_secret_name_stable_across_calls():
    """Producer (``update_secrets``) and consumer (``render_manifests``)
    must agree on the name. The helper is the single source of truth."""
    from astrolift_workflows.activities.app_lifecycle import _bindings_secret_name

    a = _bindings_secret_name("checkout")
    b = _bindings_secret_name("checkout")
    assert a == b


# ---- module-level imports do not raise --------------------------------


# ---- _apply_semantic_options (#772) ------------------------------------


def test_apply_semantic_options_ephemeral_strips_key_and_keeps_empty_spec():
    """Ephemeral mode (default) must strip the synthetic prometheus_storage
    key from the merged values and leave storageSpec empty."""
    from astrolift_workflows.activities.install_prereqs import _apply_semantic_options

    base = {
        "nodeExporter": {"enabled": False},
        "prometheus": {"prometheusSpec": {"retention": "24h", "storageSpec": {}}},
        "prometheus_storage": "ephemeral",
    }
    out = _apply_semantic_options("kube-prometheus-stack", base, {"prometheus_storage": "ephemeral"})
    assert "prometheus_storage" not in out
    assert out["prometheus"]["prometheusSpec"]["storageSpec"] == {}
    assert out["prometheus"]["prometheusSpec"]["retention"] == "24h"


def test_apply_semantic_options_efs_persistent_sets_storagespec_and_retention():
    """efs_persistent must embed the EFS PVC template and switch retention to 30d."""
    from astrolift_workflows.activities.install_prereqs import _apply_semantic_options

    base = {
        "nodeExporter": {"enabled": False},
        "prometheus": {"prometheusSpec": {"retention": "24h", "storageSpec": {}}},
        "prometheus_storage": "efs_persistent",
    }
    out = _apply_semantic_options("kube-prometheus-stack", base, {"prometheus_storage": "efs_persistent"})
    assert "prometheus_storage" not in out
    spec = out["prometheus"]["prometheusSpec"]
    assert spec["retention"] == "30d"
    assert spec["storageSpec"]["volumeClaimTemplate"]["spec"]["storageClassName"] == "efs-prometheus"


def test_apply_semantic_options_filestore_persistent():
    """filestore_persistent must reference the filestore-prometheus StorageClass."""
    from astrolift_workflows.activities.install_prereqs import _apply_semantic_options

    base = {"prometheus": {"prometheusSpec": {"storageSpec": {}}}}
    out = _apply_semantic_options(
        "kube-prometheus-stack", base, {"prometheus_storage": "filestore_persistent"}
    )
    sc = out["prometheus"]["prometheusSpec"]["storageSpec"]["volumeClaimTemplate"]["spec"]["storageClassName"]
    assert sc == "filestore-prometheus"


def test_apply_semantic_options_azurefile_persistent():
    """azurefile_persistent must reference the azurefile-prometheus StorageClass."""
    from astrolift_workflows.activities.install_prereqs import _apply_semantic_options

    base = {"prometheus": {"prometheusSpec": {"storageSpec": {}}}}
    out = _apply_semantic_options(
        "kube-prometheus-stack", base, {"prometheus_storage": "azurefile_persistent"}
    )
    sc = out["prometheus"]["prometheusSpec"]["storageSpec"]["volumeClaimTemplate"]["spec"]["storageClassName"]
    assert sc == "azurefile-prometheus"


def test_apply_semantic_options_unknown_mode_strips_key():
    """An unknown prometheus_storage value is treated as ephemeral — the key
    is stripped and storageSpec is left unchanged."""
    from astrolift_workflows.activities.install_prereqs import _apply_semantic_options

    base = {"prometheus": {"prometheusSpec": {"storageSpec": {}}}, "prometheus_storage": "unknown_mode"}
    out = _apply_semantic_options("kube-prometheus-stack", base, {"prometheus_storage": "unknown_mode"})
    assert "prometheus_storage" not in out
    assert out["prometheus"]["prometheusSpec"]["storageSpec"] == {}


def test_apply_semantic_options_noop_for_other_components():
    """The function must be a no-op for any component other than
    kube-prometheus-stack."""
    from astrolift_workflows.activities.install_prereqs import _apply_semantic_options

    values = {"mode": "efs_persistent"}  # same key, different component
    out = _apply_semantic_options("cert-manager", values, {"mode": "efs_persistent"})
    assert out == values


def test_apply_semantic_options_does_not_mutate_base():
    """Pure-function semantic — base dict must not be modified in place."""
    from astrolift_workflows.activities.install_prereqs import _apply_semantic_options

    base = {"prometheus": {"prometheusSpec": {"storageSpec": {}}}}
    original_base = {"prometheus": {"prometheusSpec": {"storageSpec": {}}}}
    _apply_semantic_options("kube-prometheus-stack", base, {"prometheus_storage": "efs_persistent"})
    assert base == original_base


# ---- module-level imports do not raise --------------------------------


def test_install_prereqs_module_imports_clean():
    """Smoke: a syntax/import error in install_prereqs.py would
    surface here without needing a worker boot."""
    import astrolift_workflows.activities.install_prereqs as m

    assert hasattr(m, "_merge_helm_values")
    assert hasattr(m, "_apply_semantic_options")
    assert hasattr(m, "install_cluster_prereqs")


def test_app_teardown_module_imports_clean():
    import astrolift_workflows.activities.app_teardown as m

    for fn in (
        "mark_app_tearing_down",
        "mark_app_deregistered",
        "list_app_managed_service_ids",
        "delete_app_namespaces",
        "revoke_app_deploy_tokens",
        "soft_delete_app_records",
    ):
        assert hasattr(m, fn)


def test_workflow_modules_import_clean():
    """The workflow modules import their activities + inputs at module
    scope; a typo there breaks the worker. Catch it at unit-test time."""
    import astrolift_workflows.workflows.deprovision_managed_service  # noqa: F401
    import astrolift_workflows.workflows.install_cluster_prereqs  # noqa: F401
    import astrolift_workflows.workflows.tear_down_app  # noqa: F401


# ---- _provision_ebs_csi_irsa_role (#1032) ------------------------------
#
# #1024 shipped IRSADriver.provision_ebs_csi_role but never called it, so the
# EBS-CSI controller SA pointed at a role nothing created and PVCs hung. These
# tests pin the wiring: the install flow mints the role (scoped to the
# discovered OIDC issuer + the astrolift-system:ebs-csi-controller-sa subject) when
# the component is enabled, is idempotent, and is a no-op when it's disabled.

import json  # noqa: E402


class _RecordingIam:
    """Moto-free recording IAM double covering the provision path
    (create_role + get/update trust + attach_role_policy). Mirrors the fake
    in providers/tests/aws/test_identity_irsa.py so the assertions can reach
    the real IRSADriver trust policy without moto installed."""

    class exceptions:  # noqa: N801
        class EntityAlreadyExistsException(Exception):  # noqa: N818
            pass

        class NoSuchEntityException(Exception):  # noqa: N818
            pass

    def __init__(self) -> None:
        self.roles: dict[str, dict] = {}
        self.attached: dict[str, list[str]] = {}

    def create_role(self, **kwargs) -> dict:  # noqa: N803 (boto3 PascalCase kwargs)
        name = kwargs["RoleName"]
        if name in self.roles:
            raise self.exceptions.EntityAlreadyExistsException(name)
        arn = f"arn:aws:iam::123456789012:role/{name}"
        self.roles[name] = {"trust": json.loads(kwargs["AssumeRolePolicyDocument"]), "arn": arn}
        return {"Role": {"Arn": arn}}

    def get_role(self, **kwargs) -> dict:
        name = kwargs["RoleName"]
        if name not in self.roles:
            raise self.exceptions.NoSuchEntityException(name)
        return {
            "Role": {
                "AssumeRolePolicyDocument": self.roles[name]["trust"],
                "Arn": self.roles[name]["arn"],
            }
        }

    def update_assume_role_policy(self, **kwargs) -> None:
        self.roles[kwargs["RoleName"]]["trust"] = json.loads(kwargs["PolicyDocument"])

    def put_role_policy(self, **kwargs) -> None:
        pass

    def attach_role_policy(self, **kwargs) -> None:
        name = kwargs["RoleName"]
        if name not in self.roles:
            raise self.exceptions.NoSuchEntityException(name)
        self.attached.setdefault(name, []).append(kwargs["PolicyArn"])


class _FakeCluster:
    """Minimal stand-in for a TenantCluster row — just the surface the
    provision helper + _ensure_cluster_oidc_issuer touch."""

    def __init__(self, auth_config: dict) -> None:
        self.auth_config = auth_config
        self.provider_config = {"account_id": "123456789012", "region": "us-west-2"}
        self.provider_plugin = type("PP", (), {"slug": "aws"})()
        self.region = "us-west-2"
        self.slug = "astrolift-eks"
        self.saved_fields: list[str] = []

    def save(self, update_fields=None) -> None:  # noqa: ANN001
        self.saved_fields = list(update_fields or [])


_DISCOVERED_ISSUER = "oidc.eks.us-west-2.amazonaws.com/id/ABC"


def _patch_driver_and_issuer(monkeypatch, iam: _RecordingIam) -> None:
    """Wire discovery (returns a known issuer) and driver_for_capability
    (builds a real IRSADriver from the cluster's *cached* issuer + the
    recording IAM). Building from auth_config proves the helper discovered +
    cached the issuer before resolving the driver."""
    import aws.identity_irsa as irsa

    import core.app_deploy as app_deploy

    monkeypatch.setattr(irsa, "discover_oidc_issuer", lambda region, name: _DISCOVERED_ISSUER)

    def _fake_driver(cluster, capability):  # noqa: ANN001
        assert capability == "identity"
        return irsa.IRSADriver(
            config=irsa.IRSAConfig(
                region="us-west-2",
                account_id="123456789012",
                cluster_oidc_issuer=cluster.auth_config["cluster_oidc_issuer"],
            ),
            iam_client=iam,
        )

    monkeypatch.setattr(app_deploy, "driver_for_capability", _fake_driver)


def test_provision_ebs_csi_role_minted_when_enabled(monkeypatch):
    """When the aws-ebs-csi-driver component is selected, the helper discovers
    the OIDC issuer and mints the controller role bound to that issuer +
    astrolift-system:ebs-csi-controller-sa, with AmazonEBSCSIDriverPolicy attached."""
    from astrolift_workflows.activities.install_prereqs import _provision_ebs_csi_irsa_role

    iam = _RecordingIam()
    _patch_driver_and_issuer(monkeypatch, iam)
    cluster = _FakeCluster({"cluster_name": "astrolift-eks"})

    arn = _provision_ebs_csi_irsa_role(cluster, {"aws-ebs-csi-driver", "metrics-server"})

    # Role name matches the SA annotation convention <cluster_name>-<key>.
    role = "astrolift-eks-aws-ebs-csi-driver"
    assert arn == f"arn:aws:iam::123456789012:role/{role}"
    # Discovery ran + cached the issuer on the row before the driver was built.
    assert cluster.auth_config["cluster_oidc_issuer"] == _DISCOVERED_ISSUER
    assert cluster.saved_fields == ["auth_config"]
    # Trust scoped to the discovered issuer + the chart's controller SA.
    cond = iam.roles[role]["trust"]["Statement"][0]["Condition"]["StringEquals"]
    assert cond[f"{_DISCOVERED_ISSUER}:sub"] == "system:serviceaccount:astrolift-system:ebs-csi-controller-sa"
    assert cond[f"{_DISCOVERED_ISSUER}:aud"] == "sts.amazonaws.com"
    assert "arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy" in iam.attached[role]


def test_provision_ebs_csi_role_idempotent(monkeypatch):
    """Re-running converges to a single trust subject (not a duplicated list)
    and returns the same ARN — a second prereqs run must not error."""
    from astrolift_workflows.activities.install_prereqs import _provision_ebs_csi_irsa_role

    iam = _RecordingIam()
    _patch_driver_and_issuer(monkeypatch, iam)
    cluster = _FakeCluster({"cluster_name": "astrolift-eks"})

    a = _provision_ebs_csi_irsa_role(cluster, {"aws-ebs-csi-driver"})
    b = _provision_ebs_csi_irsa_role(cluster, {"aws-ebs-csi-driver"})

    assert a == b
    cond = iam.roles["astrolift-eks-aws-ebs-csi-driver"]["trust"]["Statement"][0]["Condition"]["StringEquals"]
    assert cond[f"{_DISCOVERED_ISSUER}:sub"] == "system:serviceaccount:astrolift-system:ebs-csi-controller-sa"


def test_provision_ebs_csi_role_noop_when_disabled(monkeypatch):
    """When the component is NOT selected the helper short-circuits — it must
    never reach issuer discovery or the identity driver."""
    import aws.identity_irsa as irsa

    import core.app_deploy as app_deploy
    from astrolift_workflows.activities.install_prereqs import _provision_ebs_csi_irsa_role

    def _boom(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("must not be called when the component is disabled")

    monkeypatch.setattr(irsa, "discover_oidc_issuer", _boom)
    monkeypatch.setattr(app_deploy, "driver_for_capability", _boom)
    cluster = _FakeCluster({"cluster_name": "astrolift-eks"})

    assert _provision_ebs_csi_irsa_role(cluster, {"metrics-server", "external-dns"}) is None
    assert cluster.auth_config == {"cluster_name": "astrolift-eks"}


def test_provision_ebs_csi_role_noop_on_non_aws(monkeypatch):
    """Other clouds use their own CSI path — the AWS EBS mint is gated on the
    provider plugin slug, so a non-AWS cluster is a no-op."""
    import core.app_deploy as app_deploy
    from astrolift_workflows.activities.install_prereqs import _provision_ebs_csi_irsa_role

    def _boom(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("must not mint an AWS role on a non-AWS cluster")

    monkeypatch.setattr(app_deploy, "driver_for_capability", _boom)
    cluster = _FakeCluster({"cluster_name": "gke-x"})
    cluster.provider_plugin = type("PP", (), {"slug": "gcp"})()

    assert _provision_ebs_csi_irsa_role(cluster, {"aws-ebs-csi-driver"}) is None
