"""Unit tests for the pure helpers in ``install_prereqs.py`` (#66).

Activity bodies need Django + a TenantCluster row + plugin loader,
so they live in integration tests. The pure helpers — value merging
and the synthetic bindings-secret name — are isolated here and run
without any platform context.
"""

from __future__ import annotations

import pytest

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
    (create_role + get/update trust + inline policies + attach_role_policy). Mirrors the fake
    in providers/tests/aws/test_identity_irsa.py so the assertions can reach
    the real IRSADriver trust policy without moto installed."""

    class exceptions:  # noqa: N801
        class EntityAlreadyExistsException(Exception):  # noqa: N818
            pass

        class NoSuchEntityException(Exception):  # noqa: N818
            pass

    def __init__(self) -> None:
        self.roles: dict[str, dict] = {}
        self.inline_policies: dict[tuple[str, str], dict] = {}
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
        name = kwargs["RoleName"]
        if name not in self.roles:
            raise self.exceptions.NoSuchEntityException(name)
        self.inline_policies[(name, kwargs["PolicyName"])] = json.loads(kwargs["PolicyDocument"])

    def delete_role_policy(self, **kwargs) -> None:
        key = (kwargs["RoleName"], kwargs["PolicyName"])
        if key not in self.inline_policies:
            raise self.exceptions.NoSuchEntityException(kwargs["PolicyName"])
        del self.inline_policies[key]

    def attach_role_policy(self, **kwargs) -> None:
        name = kwargs["RoleName"]
        if name not in self.roles:
            raise self.exceptions.NoSuchEntityException(name)
        policies = self.attached.setdefault(name, [])
        if kwargs["PolicyArn"] not in policies:
            policies.append(kwargs["PolicyArn"])


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

    def _fake_discover(region, name, *, credential=None):  # noqa: ANN001
        # Asserted rather than ignored: this path discovers the issuer from
        # the tenant's own EKS cluster, so reaching it with the control
        # plane's identity would fail on a cluster that declares a role
        # (#1422). A stub that swallowed the keyword would hide that.
        assert credential is not None, "discovery must be told which identity to use"
        return _DISCOVERED_ISSUER

    monkeypatch.setattr(irsa, "discover_oidc_issuer", _fake_discover)

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
    role = "astrolift-eks-aws-ebs-csi-driver"
    iam.put_role_policy(
        RoleName=role,
        PolicyName="astrolift-workload-policy",
        PolicyDocument=json.dumps({"Version": "2012-10-17", "Statement": [{"Action": "s3:*"}]}),
    )
    b = _provision_ebs_csi_irsa_role(cluster, {"aws-ebs-csi-driver"})
    assert (role, "astrolift-workload-policy") not in iam.inline_policies
    c = _provision_ebs_csi_irsa_role(cluster, {"aws-ebs-csi-driver"})

    assert a == b == c
    assert iam.attached[role] == ["arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"]
    assert (role, "astrolift-workload-policy") not in iam.inline_policies
    cond = iam.roles[role]["trust"]["Statement"][0]["Condition"]["StringEquals"]
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


# ---- _provision_aws_controller_irsa_role (#1044) -----------------------
#
# The aws-load-balancer-controller + external-dns components had no mint call
# site at all (worse than EBS-CSI's missing call: the driver had no mint fn
# either). These pin the generalized wiring: mint when selected on AWS, scope
# the trust to the chart's pinned SA subject, idempotent, no-op otherwise.


def test_provision_alb_controller_role_minted_when_enabled(monkeypatch):
    """When aws-load-balancer-controller is selected, the helper discovers the
    OIDC issuer and mints <cluster_name>-aws-load-balancer-controller bound to
    astrolift-system:aws-load-balancer-controller."""
    from astrolift_workflows.activities.install_prereqs import _provision_aws_controller_irsa_role

    iam = _RecordingIam()
    _patch_driver_and_issuer(monkeypatch, iam)
    cluster = _FakeCluster({"cluster_name": "astrolift-eks"})

    arn = _provision_aws_controller_irsa_role(
        cluster,
        {"aws-load-balancer-controller", "external-dns"},
        component_key="aws-load-balancer-controller",
        mint_method="provision_alb_controller_role",
    )

    role = "astrolift-eks-aws-load-balancer-controller"
    assert arn == f"arn:aws:iam::123456789012:role/{role}"
    assert cluster.auth_config["cluster_oidc_issuer"] == _DISCOVERED_ISSUER
    assert cluster.saved_fields == ["auth_config"]
    cond = iam.roles[role]["trust"]["Statement"][0]["Condition"]["StringEquals"]
    assert (
        cond[f"{_DISCOVERED_ISSUER}:sub"]
        == "system:serviceaccount:astrolift-system:aws-load-balancer-controller"
    )


def test_provision_external_dns_role_minted_when_enabled(monkeypatch):
    """external-dns selected → mint <cluster_name>-external-dns bound to
    astrolift-system:external-dns."""
    from astrolift_workflows.activities.install_prereqs import _provision_aws_controller_irsa_role

    iam = _RecordingIam()
    _patch_driver_and_issuer(monkeypatch, iam)
    cluster = _FakeCluster({"cluster_name": "astrolift-eks"})

    arn = _provision_aws_controller_irsa_role(
        cluster,
        {"external-dns"},
        component_key="external-dns",
        mint_method="provision_external_dns_role",
    )

    role = "astrolift-eks-external-dns"
    assert arn == f"arn:aws:iam::123456789012:role/{role}"
    cond = iam.roles[role]["trust"]["Statement"][0]["Condition"]["StringEquals"]
    assert cond[f"{_DISCOVERED_ISSUER}:sub"] == "system:serviceaccount:astrolift-system:external-dns"


def test_provision_aws_controller_role_idempotent(monkeypatch):
    """Re-running converges to a single trust subject + the same ARN."""
    from astrolift_workflows.activities.install_prereqs import _provision_aws_controller_irsa_role

    iam = _RecordingIam()
    _patch_driver_and_issuer(monkeypatch, iam)
    cluster = _FakeCluster({"cluster_name": "astrolift-eks"})

    a = _provision_aws_controller_irsa_role(
        cluster,
        {"aws-load-balancer-controller"},
        component_key="aws-load-balancer-controller",
        mint_method="provision_alb_controller_role",
    )
    b = _provision_aws_controller_irsa_role(
        cluster,
        {"aws-load-balancer-controller"},
        component_key="aws-load-balancer-controller",
        mint_method="provision_alb_controller_role",
    )

    assert a == b
    cond = iam.roles["astrolift-eks-aws-load-balancer-controller"]["trust"]["Statement"][0]["Condition"][
        "StringEquals"
    ]
    assert (
        cond[f"{_DISCOVERED_ISSUER}:sub"]
        == "system:serviceaccount:astrolift-system:aws-load-balancer-controller"
    )


def test_provision_s3_csi_role_minted_when_enabled(monkeypatch):
    """aws-mountpoint-s3-csi-driver selected → mint
    <cluster_name>-aws-mountpoint-s3-csi-driver bound to
    astrolift-system:s3-csi-driver-sa (#1675)."""
    from astrolift_workflows.activities.install_prereqs import _provision_aws_controller_irsa_role

    iam = _RecordingIam()
    _patch_driver_and_issuer(monkeypatch, iam)
    cluster = _FakeCluster({"cluster_name": "astrolift-eks"})

    arn = _provision_aws_controller_irsa_role(
        cluster,
        {"aws-mountpoint-s3-csi-driver"},
        component_key="aws-mountpoint-s3-csi-driver",
        mint_method="provision_s3_csi_role",
    )

    role = "astrolift-eks-aws-mountpoint-s3-csi-driver"
    assert arn == f"arn:aws:iam::123456789012:role/{role}"
    cond = iam.roles[role]["trust"]["Statement"][0]["Condition"]["StringEquals"]
    assert cond[f"{_DISCOVERED_ISSUER}:sub"] == "system:serviceaccount:astrolift-system:s3-csi-driver-sa"


def test_provision_aws_controller_role_noop_when_disabled(monkeypatch):
    """Component not selected → short-circuit before discovery / driver."""
    import aws.identity_irsa as irsa

    import core.app_deploy as app_deploy
    from astrolift_workflows.activities.install_prereqs import _provision_aws_controller_irsa_role

    def _boom(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("must not be called when the component is disabled")

    monkeypatch.setattr(irsa, "discover_oidc_issuer", _boom)
    monkeypatch.setattr(app_deploy, "driver_for_capability", _boom)
    cluster = _FakeCluster({"cluster_name": "astrolift-eks"})

    result = _provision_aws_controller_irsa_role(
        cluster,
        {"metrics-server", "aws-ebs-csi-driver"},
        component_key="external-dns",
        mint_method="provision_external_dns_role",
    )
    assert result is None
    assert cluster.auth_config == {"cluster_name": "astrolift-eks"}


def test_provision_aws_controller_role_noop_on_non_aws(monkeypatch):
    """Non-AWS cluster → no AWS role minted even when the key is selected."""
    import core.app_deploy as app_deploy
    from astrolift_workflows.activities.install_prereqs import _provision_aws_controller_irsa_role

    def _boom(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("must not mint an AWS role on a non-AWS cluster")

    monkeypatch.setattr(app_deploy, "driver_for_capability", _boom)
    cluster = _FakeCluster({"cluster_name": "gke-x"})
    cluster.provider_plugin = type("PP", (), {"slug": "gcp"})()

    assert (
        _provision_aws_controller_irsa_role(
            cluster,
            {"aws-load-balancer-controller"},
            component_key="aws-load-balancer-controller",
            mint_method="provision_alb_controller_role",
        )
        is None
    )


# ---- post_install_manifests wiring (Knative Serving) -------------------
#
# A BootstrapComponent may carry raw k8s objects applied (idempotent SSA)
# after its HelmRelease — the canonical case is the knative-operator chart
# plus a KnativeServing CR + its namespace. These pin the ordering (depends_on
# across components, foundational kinds first within one), the best-effort
# failure surfacing, and the "operator CRD not ready yet → retry" signal.

from _sdk.cluster import ApplyError, ApplyResult, BootstrapComponent, DeleteResult  # noqa: E402

_KN_NS = {
    "apiVersion": "v1",
    "kind": "Namespace",
    "metadata": {"name": "knative-serving"},
}
_KN_CR = {
    "apiVersion": "operator.knative.dev/v1beta1",
    "kind": "KnativeServing",
    "metadata": {"name": "knative-serving", "namespace": "knative-serving"},
}


def _pi_component(key, manifests, depends_on=None):  # noqa: ANN001
    """A BootstrapComponent carrying only the fields the post-install path
    reads (key, depends_on, post_install_manifests)."""
    return BootstrapComponent(
        key=key,
        title=key,
        default_enabled=False,
        rationale="",
        post_install_manifests=manifests,
        depends_on=depends_on or [],
    )


class _FakeDriver:
    """Records apply_manifests calls and returns a canned/computed ApplyResult.

    ``result_fn(namespace, manifests) -> ApplyResult`` lets a test simulate
    partial failures (e.g. the CR fails while its Namespace succeeds)."""

    def __init__(self, result_fn=None):  # noqa: ANN001
        self.calls: list[tuple[str, str, list[dict]]] = []
        self.deletes: list[tuple[str, str, list[dict]]] = []
        self._result_fn = result_fn

    def apply_manifests(self, ctx_slug, namespace, manifests, *, dry_run=False):  # noqa: ANN001
        self.calls.append((ctx_slug, namespace, list(manifests)))
        if self._result_fn is not None:
            return self._result_fn(namespace, manifests)
        return ApplyResult(
            created=[f"{m['kind']}/{m['metadata']['name']}" for m in manifests],
            updated=[],
            unchanged=[],
            errors=[],
        )

    def delete_manifests(self, ctx_slug, namespace, manifests):  # noqa: ANN001
        """Records stale-cleanup deletes; reports everything not_found (nothing
        to actually delete in the fake), which the sync flow treats as a no-op."""
        self.deletes.append((ctx_slug, namespace, list(manifests)))
        return DeleteResult(
            deleted=[],
            not_found=[f"{m['kind']}/{m['metadata']['name']}" for m in manifests],
            errors=[],
        )

    @property
    def applied_refs(self) -> list[tuple[str, str]]:
        """(kind, name) in the order they were handed to the driver."""
        refs: list[tuple[str, str]] = []
        for _slug, _ns, manifests in self.calls:
            for m in manifests:
                refs.append((m["kind"], m["metadata"]["name"]))
        return refs


# ---- _order_by_depends_on ----------------------------------------------


def test_order_by_depends_on_places_dependency_first():
    from astrolift_workflows.activities.install_prereqs import _order_by_depends_on

    a = _pi_component("a", [])
    b = _pi_component("b", [], depends_on=["a"])
    ordered = _order_by_depends_on([b, a])  # reversed input
    assert [c.key for c in ordered] == ["a", "b"]


def test_order_by_depends_on_tolerates_cycle():
    """A dependency cycle degrades to best-effort order, not a crash."""
    from astrolift_workflows.activities.install_prereqs import _order_by_depends_on

    a = _pi_component("a", [], depends_on=["b"])
    b = _pi_component("b", [], depends_on=["a"])
    ordered = _order_by_depends_on([a, b])
    assert {c.key for c in ordered} == {"a", "b"}


def test_order_by_depends_on_ignores_missing_dep():
    from astrolift_workflows.activities.install_prereqs import _order_by_depends_on

    a = _pi_component("a", [], depends_on=["nonexistent"])
    assert [c.key for c in _order_by_depends_on([a])] == ["a"]


# ---- _post_install_namespace -------------------------------------------


def test_post_install_namespace_prefers_declared_namespace():
    from astrolift_workflows.activities.install_prereqs import _post_install_namespace

    # NS is cluster-scoped (no metadata.namespace); the CR declares one.
    assert _post_install_namespace([_KN_NS, _KN_CR], default="astrolift-system") == "knative-serving"


def test_post_install_namespace_falls_back_when_all_cluster_scoped():
    from astrolift_workflows.activities.install_prereqs import _post_install_namespace

    assert _post_install_namespace([_KN_NS], default="astrolift-system") == "astrolift-system"


# ---- _apply_post_install_manifests -------------------------------------


def test_apply_post_install_applies_namespace_and_cr():
    """The KnativeServing CR + its namespace flow through the driver, and the
    Namespace is applied before the CR even if authored CR-first."""
    from astrolift_workflows.activities.install_prereqs import _apply_post_install_manifests

    driver = _FakeDriver()
    comp = _pi_component("knative-serving", [_KN_CR, _KN_NS])  # deliberately CR-first

    crd_not_ready, errors = _apply_post_install_manifests(
        driver, "aws-prod", [comp], {"knative-serving"}, "astrolift-system"
    )

    assert crd_not_ready is False
    assert errors == []
    assert ("Namespace", "knative-serving") in driver.applied_refs
    assert ("KnativeServing", "knative-serving") in driver.applied_refs
    # Foundational-first: Namespace precedes the CR in the applied batch.
    _slug, ns, manifests = driver.calls[0]
    kinds = [m["kind"] for m in manifests]
    assert kinds.index("Namespace") < kinds.index("KnativeServing")
    # The namespaced CR drives the namespace passed to the driver.
    assert ns == "knative-serving"


def test_apply_post_install_orders_by_depends_on():
    """Across components, a dependency's manifests are applied before its
    dependent's, regardless of input order."""
    from astrolift_workflows.activities.install_prereqs import _apply_post_install_manifests

    driver = _FakeDriver()
    a = _pi_component(
        "a", [{"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "a", "namespace": "x"}}]
    )
    b = _pi_component(
        "b",
        [{"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "b", "namespace": "x"}}],
        depends_on=["a"],
    )

    _apply_post_install_manifests(driver, "s", [b, a], {"a", "b"}, "astrolift-system")

    names = [name for _kind, name in driver.applied_refs]
    assert names.index("a") < names.index("b")


def test_apply_post_install_empty_makes_no_apply_calls():
    """No regression: a component with empty post_install_manifests never
    reaches the driver."""
    from astrolift_workflows.activities.install_prereqs import _apply_post_install_manifests

    driver = _FakeDriver()
    comp = _pi_component("metrics-server", [])

    crd_not_ready, errors = _apply_post_install_manifests(
        driver, "s", [comp], {"metrics-server"}, "astrolift-system"
    )

    assert crd_not_ready is False
    assert errors == []
    assert driver.calls == []


def test_apply_post_install_skips_deselected_component():
    """A component with post_install_manifests that the operator did NOT
    select is not applied."""
    from astrolift_workflows.activities.install_prereqs import _apply_post_install_manifests

    driver = _FakeDriver()
    comp = _pi_component("knative-serving", [_KN_NS, _KN_CR])

    _apply_post_install_manifests(driver, "s", [comp], set(), "astrolift-system")

    assert driver.calls == []


def test_apply_post_install_crd_missing_signals_retry():
    """When the CR's operator CRD isn't registered yet, the Namespace still
    applies but the CR fails with 'no matches for kind' → crd_not_ready=True
    (caller raises a retriable error) and the error is surfaced."""
    from astrolift_workflows.activities.install_prereqs import _apply_post_install_manifests

    def result_fn(namespace, manifests):  # noqa: ANN001
        created: list[str] = []
        errs: list[ApplyError] = []
        for m in manifests:
            if m["kind"] == "KnativeServing":
                errs.append(
                    ApplyError(
                        kind="KnativeServing",
                        name="knative-serving",
                        namespace=namespace,
                        exception_type="ResourceNotFoundError",
                        exception_message="No matches found for operator.knative.dev/v1beta1/KnativeServing",
                        is_retryable=False,
                    )
                )
            else:
                created.append(f"{m['kind']}/{m['metadata']['name']}")
        return ApplyResult(created=created, updated=[], unchanged=[], errors=errs)

    driver = _FakeDriver(result_fn)
    comp = _pi_component("knative-serving", [_KN_NS, _KN_CR])

    crd_not_ready, errors = _apply_post_install_manifests(
        driver, "s", [comp], {"knative-serving"}, "astrolift-system"
    )

    assert crd_not_ready is True
    assert any("No matches found" in e for e in errors)


def test_apply_post_install_non_crd_error_is_best_effort():
    """A genuine (non-CRD) error on one component is surfaced but does NOT
    abort a sibling component's apply, and does not signal a retry."""
    from astrolift_workflows.activities.install_prereqs import _apply_post_install_manifests

    def result_fn(namespace, manifests):  # noqa: ANN001
        if any(m["metadata"]["name"] == "bad" for m in manifests):
            return ApplyResult(
                created=[],
                updated=[],
                unchanged=[],
                errors=[
                    ApplyError(
                        kind="ConfigMap",
                        name="bad",
                        namespace=namespace,
                        exception_type="ValidationError",
                        exception_message="invalid field xyz",
                        is_retryable=False,
                    )
                ],
            )
        return ApplyResult(
            created=[f"{m['kind']}/{m['metadata']['name']}" for m in manifests],
            updated=[],
            unchanged=[],
            errors=[],
        )

    driver = _FakeDriver(result_fn)
    bad = _pi_component(
        "bad", [{"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "bad", "namespace": "x"}}]
    )
    good = _pi_component(
        "good", [{"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "good", "namespace": "x"}}]
    )

    crd_not_ready, errors = _apply_post_install_manifests(
        driver, "s", [bad, good], {"bad", "good"}, "astrolift-system"
    )

    assert crd_not_ready is False
    assert any("invalid field xyz" in e for e in errors)
    # best-effort: 'good' still applied despite 'bad' failing.
    assert ("ConfigMap", "good") in driver.applied_refs


# ---- _install_cluster_prereqs_sync: chart-less vs chart-based -----------
#
# A chart-less component (chart_repo_url == "") must emit NO HelmRelease, yet
# still apply its post_install_manifests — the flavor the vendored Knative
# operator install uses (the operator's OCI chart isn't pullable, so the
# operator YAML ships as post_install instead of a HelmRelease). These drive
# the whole sync activity with the DB + dispatch + driver mocked to prove the
# render loop skips the HelmRelease but does not drop the post-install objects,
# that a chart-less component with EMPTY post-install is a true no-op, and that
# chart-based components are unaffected.


def _chart_component(key, *, chart="thechart", repo="https://charts.example/", version="1.0.0"):  # noqa: ANN001
    """A chart-based BootstrapComponent (emits a HelmRelease + HelmRepository)."""
    return BootstrapComponent(
        key=key,
        title=key,
        default_enabled=False,
        rationale="",
        chart_name=chart,
        chart_repo_url=repo,
        chart_version=version,
    )


def _run_install_sync(  # noqa: ANN001
    monkeypatch, components, driver, selected_keys, overrides=None, additive=False, recipe_before=frozenset()
):
    """Drive ``_install_cluster_prereqs_sync`` with the DB row, dispatch, driver,
    and context all mocked, returning its result dict. The IRSA-provision helpers
    short-circuit because none of the test component keys are the AWS controller
    keys, so no cloud calls are made."""
    import astrolift_clusters.models as models
    import core.cluster_management as cm
    from astrolift_workflows.activities import install_prereqs
    from astrolift_workflows.activities.install_prereqs import _install_cluster_prereqs_sync

    fake_cluster = type("FakeCluster", (), {"provider_plugin": type("PP", (), {"slug": "aws"})()})()
    monkeypatch.setattr(install_prereqs, "_provision_ebs_csi_irsa_role", lambda *a, **k: None)
    monkeypatch.setattr(install_prereqs, "_provision_aws_controller_irsa_role", lambda *a, **k: None)

    class _FakeManager:
        def select_related(self, *a, **k):  # noqa: ANN002, ANN003
            return self

        def get(self, **k):  # noqa: ANN003
            return fake_cluster

    class _FakeTenantCluster:
        objects = _FakeManager()

    fake_ctx = type("Ctx", (), {"slug": "aws-prod"})()

    monkeypatch.setattr(models, "TenantCluster", _FakeTenantCluster)
    monkeypatch.setattr(cm, "_driver_for_cluster", lambda cluster: driver)  # noqa: ARG005
    monkeypatch.setattr(cm, "_context_for_cluster", lambda cluster: fake_ctx)  # noqa: ARG005
    monkeypatch.setattr(cm, "bootstrap_components_dispatch", lambda cluster: components)  # noqa: ARG005
    import astrolift_clusters.recipe_detection as rd

    monkeypatch.setattr(rd, "components_installed_by_recipe", lambda cluster: set(recipe_before))  # noqa: ARG005

    return _install_cluster_prereqs_sync(1, list(selected_keys), overrides or {}, additive)


def test_install_sync_chartless_with_post_install_applies_no_helmrelease(monkeypatch):
    """A selected chart-less component emits NO HelmRelease/HelmRepository, but
    its post_install_manifests still reach the driver (the Knative flavor)."""
    pi_manifests = [
        {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "pi-ns"}},
        {"apiVersion": "x.example/v1", "kind": "Widget", "metadata": {"name": "w", "namespace": "pi-ns"}},
    ]
    comp = _pi_component("chartless-pi", pi_manifests)
    driver = _FakeDriver()

    result = _run_install_sync(monkeypatch, [comp], driver, {"chartless-pi"})

    # The HelmRelease batch (calls[0]) is empty — no chart means no release/repo.
    _slug, first_ns, first_manifests = driver.calls[0]
    assert first_ns == "astrolift-system"
    assert first_manifests == []
    # The post-install manifests DID land (a later apply call).
    assert ("Namespace", "pi-ns") in driver.applied_refs
    assert ("Widget", "w") in driver.applied_refs
    # Recorded as applied so the UI reflects the operator's selection.
    assert {"name": "chartless-pi", "version": ""} in result["applied"]


def test_install_sync_chartless_empty_post_install_is_noop(monkeypatch):
    """A selected chart-less component with EMPTY post_install is a full no-op:
    no HelmRelease and no post-install apply — nothing lands on the cluster."""
    comp = _pi_component("chartless-empty", [])
    driver = _FakeDriver()

    result = _run_install_sync(monkeypatch, [comp], driver, {"chartless-empty"})

    # The only apply call is the (empty) HelmRelease batch; no manifests applied.
    assert driver.applied_refs == []
    assert all(manifests == [] for _slug, _ns, manifests in driver.calls)
    # Still recorded as applied (the operator's choice is reflected in the UI).
    assert {"name": "chartless-empty", "version": ""} in result["applied"]


def test_install_sync_chartbased_emits_helmrelease(monkeypatch):
    """Regression: a chart-based component still emits its HelmRepository +
    HelmRelease and runs no post-install pass."""
    comp = _chart_component("chartbased")
    driver = _FakeDriver()

    result = _run_install_sync(monkeypatch, [comp], driver, {"chartbased"})

    # Exactly one apply call (no post-install) carrying the repo + release.
    assert len(driver.calls) == 1
    _slug, _ns, manifests = driver.calls[0]
    kinds = [m["kind"] for m in manifests]
    assert "HelmRepository" in kinds
    assert "HelmRelease" in kinds
    release = next(m for m in manifests if m["kind"] == "HelmRelease")
    assert release["metadata"]["name"] == "astrolift-chartbased"
    assert release["spec"]["chart"]["spec"]["chart"] == "thechart"
    assert {"name": "chartbased", "version": "1.0.0"} in result["applied"]


def test_install_sync_mixed_chartbased_and_chartless_pi(monkeypatch):
    """Chart-based and chart-less-with-post-install coexist: the HelmRelease
    batch carries only the chart component's release (never one for the
    chart-less component), and the chart-less component's post-install still
    lands."""
    pi = _pi_component(
        "chartless-pi",
        [{"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "pi-ns"}}],
    )
    chart = _chart_component("chartbased")
    driver = _FakeDriver()

    result = _run_install_sync(monkeypatch, [chart, pi], driver, {"chartbased", "chartless-pi"})

    _slug, _ns, batch = driver.calls[0]
    release_names = [m["metadata"]["name"] for m in batch if m["kind"] == "HelmRelease"]
    assert release_names == ["astrolift-chartbased"]  # no release for the chart-less one
    # The chart-less component's post-install still lands (a later apply call).
    assert ("Namespace", "pi-ns") in driver.applied_refs
    assert {"name": "chartbased", "version": "1.0.0"} in result["applied"]
    assert {"name": "chartless-pi", "version": ""} in result["applied"]


# ---- additive runs (#2130) ----------------------------------------------


def test_a_normal_run_deletes_the_releases_it_did_not_select(monkeypatch):
    """The baseline an additive run departs from: deselected means removed."""
    driver = _FakeDriver()

    _run_install_sync(monkeypatch, [_chart_component("edge"), _chart_component("other")], driver, {"edge"})

    deleted = {m["metadata"]["name"] for _slug, _ns, manifests in driver.deletes for m in manifests}
    assert "astrolift-other" in deleted


def test_an_additive_run_applies_its_selection_and_deletes_nothing(monkeypatch):
    """The control plane's own edge install selects only the edge. Were it a
    normal run, it would delete every other release the operator installed."""
    driver = _FakeDriver()

    result = _run_install_sync(
        monkeypatch,
        [_chart_component("edge"), _chart_component("other")],
        driver,
        {"edge"},
        additive=True,
    )

    assert driver.deletes == []
    assert result["deleted"] == []
    assert ("HelmRelease", "astrolift-edge") in driver.applied_refs
    assert ("HelmRelease", "astrolift-other") not in driver.applied_refs


def _probe_reports(monkeypatch, capabilities):  # noqa: ANN001
    import core.cluster_management as cm

    monkeypatch.setattr(cm, "probe_cluster_capabilities_dispatch", lambda cluster: capabilities)  # noqa: ARG005


def _edge_recipe():
    return [
        _chart_component("envoy-gateway"),
        _chart_component("aws-load-balancer-controller"),
        _chart_component("external-dns"),
        _chart_component("cert-manager"),
    ]


def test_an_additive_edge_run_brings_the_controllers_a_fresh_cluster_lacks(monkeypatch):
    """A cluster straight from the installer runs no ALB controller and no
    external-dns, and the edge's Gateway would sit behind no load balancer."""
    _probe_reports(monkeypatch, {"installed_crds": [], "external_dns": {"installed": False}})
    driver = _FakeDriver()

    result = _run_install_sync(monkeypatch, _edge_recipe(), driver, {"envoy-gateway"}, additive=True)

    assert {a["name"] for a in result["applied"]} == {
        "envoy-gateway",
        "aws-load-balancer-controller",
        "external-dns",
    }
    assert driver.deletes == []


def test_an_additive_edge_run_never_duplicates_a_controller_already_running(monkeypatch):
    """CONFLICT's shape: both controllers hand-installed into kube-system."""
    _probe_reports(
        monkeypatch,
        {
            "installed_crds": ["targetgroupbindings.elbv2.k8s.aws"],
            "external_dns": {"installed": True, "provider": None},
        },
    )
    driver = _FakeDriver()

    result = _run_install_sync(monkeypatch, _edge_recipe(), driver, {"envoy-gateway"}, additive=True)

    assert [a["name"] for a in result["applied"]] == ["envoy-gateway"]


def test_a_failed_probe_stops_the_additive_edge_run(monkeypatch):
    """Guessing "absent" would install a second controller over a hand-installed one."""
    import core.cluster_management as cm

    def _boom(cluster):  # noqa: ANN001, ARG001
        raise ConnectionError("apiserver unreachable")

    monkeypatch.setattr(cm, "probe_cluster_capabilities_dispatch", _boom)
    driver = _FakeDriver()

    with pytest.raises(ConnectionError):
        _run_install_sync(monkeypatch, _edge_recipe(), driver, {"envoy-gateway"}, additive=True)
    assert driver.calls == []


def test_an_operator_run_never_probes_or_adds_controllers(monkeypatch):
    import core.cluster_management as cm

    monkeypatch.setattr(cm, "probe_cluster_capabilities_dispatch", lambda cluster: pytest.fail("probed"))  # noqa: ARG005
    driver = _FakeDriver()

    result = _run_install_sync(monkeypatch, _edge_recipe(), driver, {"envoy-gateway"})

    assert [a["name"] for a in result["applied"]] == ["envoy-gateway"]


def test_an_additive_run_records_the_whole_recipe_not_just_its_own_releases(monkeypatch):
    """Recording only the edge would make the next operator run read the rest
    of the recipe as foreign, leave it unchecked, and delete it."""
    _probe_reports(
        monkeypatch,
        {"installed_crds": ["targetgroupbindings.elbv2.k8s.aws"], "external_dns": {"installed": True}},
    )
    driver = _FakeDriver()

    result = _run_install_sync(
        monkeypatch,
        _edge_recipe(),
        driver,
        {"envoy-gateway"},
        additive=True,
        recipe_before={"cert-manager", "external-dns"},
    )

    assert [a["name"] for a in result["applied"]] == ["envoy-gateway"]
    assert {r["name"] for r in result["recorded"]} == {"envoy-gateway", "cert-manager", "external-dns"}


def test_an_operator_run_records_what_it_applied(monkeypatch):
    driver = _FakeDriver()

    result = _run_install_sync(
        monkeypatch, _edge_recipe(), driver, {"cert-manager"}, recipe_before={"external-dns"}
    )

    assert result["recorded"] == result["applied"]


# ---- what the install withholds (calliope-installer#447) -------------------


@pytest.mark.parametrize("withheld", [None, "dns,load_balancers"])
def test_install_sync_skips_controllers_the_install_withholds(monkeypatch, withheld):
    """external-dns and the ALB controller act for DNS and load balancers. With
    those withheld their IRSA roles are denied, so the run never renders them,
    however they were selected (the edge install adds them on its own)."""
    if withheld:
        monkeypatch.setenv("ASTROLIFT_WITHHELD_CAPABILITIES", withheld)
    else:
        monkeypatch.delenv("ASTROLIFT_WITHHELD_CAPABILITIES", raising=False)
    keys = ("external-dns", "aws-load-balancer-controller", "cert-manager")
    driver = _FakeDriver()

    result = _run_install_sync(monkeypatch, [_chart_component(k) for k in keys], driver, set(keys))

    releases = {
        m["metadata"]["name"] for _s, _n, ms in driver.calls for m in ms if m["kind"] == "HelmRelease"
    }
    if withheld:
        assert releases == {"astrolift-cert-manager"}
        assert {"external-dns", "aws-load-balancer-controller"} <= set(result["skipped"])
    else:
        assert releases == {f"astrolift-{k}" for k in keys}
