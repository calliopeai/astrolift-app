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


def test_install_prereqs_module_imports_clean():
    """Smoke: a syntax/import error in install_prereqs.py would
    surface here without needing a worker boot."""
    import astrolift_workflows.activities.install_prereqs as m

    assert hasattr(m, "_merge_helm_values")
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
