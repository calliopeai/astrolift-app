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
    out = _apply_semantic_options("kube-prometheus-stack", base, {"prometheus_storage": "filestore_persistent"})
    sc = out["prometheus"]["prometheusSpec"]["storageSpec"]["volumeClaimTemplate"]["spec"]["storageClassName"]
    assert sc == "filestore-prometheus"


def test_apply_semantic_options_azurefile_persistent():
    """azurefile_persistent must reference the azurefile-prometheus StorageClass."""
    from astrolift_workflows.activities.install_prereqs import _apply_semantic_options

    base = {"prometheus": {"prometheusSpec": {"storageSpec": {}}}}
    out = _apply_semantic_options("kube-prometheus-stack", base, {"prometheus_storage": "azurefile_persistent"})
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
