"""Tests for the cert-state lifecycle + BYO upload + renderer wiring
that completes the #397 custom-domain handshake feature.

Three layers exercised here:

1. ``_pick_tls_strategy`` — the platform-managed vs external matrix.
2. ``upload_custom_domain_certificate`` GraphQL mutation — validation
   + state transition into ``CertificateState.BYO``.
3. ``_render_app_ingresses_and_tls`` — per-domain Ingress + TLS Secret
   emission for the deploy renderer (ACTIVE vs BYO vs in-flight).
"""

from __future__ import annotations

import pytest

from astrolift_lifecycle.models import CustomDomain
from astrolift_lifecycle.schema.mutations import (
    EnvironmentByIdInput,
    LifecycleMutation,
    UploadCustomDomainCertificateInput,
)
from astrolift_workflows.activities.app_lifecycle import (
    _render_app_ingresses_and_tls,
)
from astrolift_workflows.activities.custom_domain import (
    _pick_tls_strategy,
)
from core.mutations import ErrorCode
from core.permissions import Permission
from core.tenancy import TenantContext, tenant_context


def _grant_update(resolver):
    resolver.grant(Permission.APP_UPDATE)


def _tenant_for(org, actor):
    return tenant_context(
        TenantContext(organization_id=org.id, actor_user_id=actor.id),
    )


# ---- _pick_tls_strategy ---------------------------------------------------


@pytest.mark.parametrize(
    "cloud,managed_zone,expected",
    [
        ("aws", True, "acm_dns_validated"),
        ("gcp", True, "gcp_managed_cert"),
        ("azure", True, "azure_managed_cert"),
        ("k8s_native", True, "letsencrypt"),
        # External zone path — AWS lands on BYO because ACM can't HTTP-01.
        ("aws", False, "provided"),
        ("gcp", False, "letsencrypt_http01"),
        ("azure", False, "letsencrypt_http01"),
        ("k8s_native", False, "letsencrypt_http01"),
        # Unknown plugins shouldn't crash — pick the safest default.
        ("custom", True, "letsencrypt"),
        ("custom", False, "letsencrypt_http01"),
    ],
)
def test_pick_tls_strategy_matrix(cloud, managed_zone, expected):
    assert (
        _pick_tls_strategy(
            plugin_slug=cloud,
            is_platform_managed_zone=managed_zone,
        )
        == expected
    )


# ---- uploadCustomDomainCertificate mutation -------------------------------

PEM_CERT_OK = (
    "-----BEGIN CERTIFICATE-----\n"
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA\n"
    "-----END CERTIFICATE-----\n"
)
PEM_KEY_OK = (
    "-----BEGIN PRIVATE KEY-----\n" "MIIBVQIBADANBgkqhkiG9w0BAQEFAASCAT8w\n" "-----END PRIVATE KEY-----\n"
)


@pytest.fixture
def custom_domain(app):
    return CustomDomain.objects.create(
        registered_app=app,
        hostname="checkout.acme.com",
        validation_status=CustomDomain.ValidationStatus.VALIDATED,
        validation_method=CustomDomain.ValidationMethod.DNS_TXT,
        is_active=True,
    )


@pytest.mark.django_db
def test_upload_byo_cert_flips_state(
    custom_domain,
    fake_info,
    org,
    actor,
    permission_resolver,
):
    _grant_update(permission_resolver)
    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mutation.upload_custom_domain_certificate(
            info=fake_info,
            input=UploadCustomDomainCertificateInput(
                id=str(custom_domain.guid),
                certificate_pem=PEM_CERT_OK,
                private_key_pem=PEM_KEY_OK,
            ),
        )
    assert result.ok is True
    custom_domain.refresh_from_db()
    assert custom_domain.certificate_state == CustomDomain.CertificateState.BYO
    assert custom_domain.byo_certificate_pem.startswith("-----BEGIN CERTIFICATE-----")
    assert "PRIVATE KEY" in custom_domain.byo_certificate_pem
    assert custom_domain.byo_certificate_uploaded_at is not None
    assert custom_domain.last_certificate_error == ""
    # BYO supersedes any auto-issued cert id — the renderer reads the
    # uploaded PEM directly.
    assert custom_domain.certificate_id == ""


@pytest.mark.django_db
def test_upload_byo_cert_validation_rejects_garbage_cert(
    custom_domain,
    fake_info,
    org,
    actor,
    permission_resolver,
):
    _grant_update(permission_resolver)
    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mutation.upload_custom_domain_certificate(
            info=fake_info,
            input=UploadCustomDomainCertificateInput(
                id=str(custom_domain.guid),
                certificate_pem="not a pem",
                private_key_pem=PEM_KEY_OK,
            ),
        )
    assert result.ok is False
    assert any("certificatePem" in (e.field or "") for e in result.errors)
    custom_domain.refresh_from_db()
    # Failed validation must not have flipped state.
    assert custom_domain.certificate_state == CustomDomain.CertificateState.NOT_REQUESTED


@pytest.mark.django_db
def test_upload_byo_cert_validation_rejects_garbage_key(
    custom_domain,
    fake_info,
    org,
    actor,
    permission_resolver,
):
    _grant_update(permission_resolver)
    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mutation.upload_custom_domain_certificate(
            info=fake_info,
            input=UploadCustomDomainCertificateInput(
                id=str(custom_domain.guid),
                certificate_pem=PEM_CERT_OK,
                private_key_pem="not a key",
            ),
        )
    assert result.ok is False
    assert any("privateKeyPem" in (e.field or "") for e in result.errors)


@pytest.mark.django_db
def test_upload_byo_cert_clears_prior_failure(
    custom_domain,
    fake_info,
    org,
    actor,
    permission_resolver,
):
    _grant_update(permission_resolver)
    custom_domain.certificate_state = CustomDomain.CertificateState.FAILED
    custom_domain.last_certificate_error = "ACME rate limit exceeded"
    custom_domain.save()
    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mutation.upload_custom_domain_certificate(
            info=fake_info,
            input=UploadCustomDomainCertificateInput(
                id=str(custom_domain.guid),
                certificate_pem=PEM_CERT_OK,
                private_key_pem=PEM_KEY_OK,
            ),
        )
    assert result.ok is True
    custom_domain.refresh_from_db()
    assert custom_domain.certificate_state == CustomDomain.CertificateState.BYO
    assert custom_domain.last_certificate_error == ""


# ---- _render_app_ingresses_and_tls ----------------------------------------


def _make_manifest(port: int = 8080):
    """Smallest NormalizedManifest with one public deployment worker."""
    from astrolift_manifest.types import (
        ContainerManifest,
        NormalizedManifest,
        WorkloadManifest,
    )

    return NormalizedManifest(
        name="hello-app",
        workloads=(
            WorkloadManifest(
                name="web",
                kind="deployment",
                replicas=1,
                containers=(
                    ContainerManifest(
                        name="web",
                        port=port,
                        is_primary=True,
                    ),
                ),
            ),
        ),
        managed_services=(),
        defaults_applied=(),
        serialized={},
    )


@pytest.fixture
def deployment(app, env):
    from astrolift_lifecycle.models import Deployment

    return Deployment.objects.create(
        registered_app=app,
        app_environment=env,
        triggered_by_user_id=None,
        trigger_kind="manual",
        status=Deployment.Status.PENDING.value,
        image_tag="v1",
    )


@pytest.mark.django_db
def test_render_ingresses_skips_unvalidated(app, deployment):
    """Domains still pending validation must not produce an Ingress —
    we never route traffic at a hostname whose cert isn't usable."""
    CustomDomain.objects.create(
        registered_app=app,
        hostname="api.acme.com",
        validation_status=CustomDomain.ValidationStatus.PENDING,
        certificate_state=CustomDomain.CertificateState.NOT_REQUESTED,
        is_active=True,
    )
    out = _render_app_ingresses_and_tls(deployment.pk, "ns", _make_manifest())
    assert out == []


@pytest.mark.django_db
def test_render_ingresses_skips_issuing(app, deployment):
    """Cert in flight (state=issuing) → still no ingress."""
    CustomDomain.objects.create(
        registered_app=app,
        hostname="api.acme.com",
        validation_status=CustomDomain.ValidationStatus.VALIDATED,
        certificate_state=CustomDomain.CertificateState.ISSUING,
        is_active=True,
    )
    out = _render_app_ingresses_and_tls(deployment.pk, "ns", _make_manifest())
    assert out == []


@pytest.mark.django_db
def test_render_ingresses_skips_failed(app, deployment):
    """FAILED state → no ingress; operator must BYO or fix the cause."""
    CustomDomain.objects.create(
        registered_app=app,
        hostname="api.acme.com",
        validation_status=CustomDomain.ValidationStatus.VALIDATED,
        certificate_state=CustomDomain.CertificateState.FAILED,
        last_certificate_error="rate limited",
        is_active=True,
    )
    out = _render_app_ingresses_and_tls(deployment.pk, "ns", _make_manifest())
    assert out == []


@pytest.mark.django_db
def test_render_ingress_active_platform_managed(app, deployment):
    """Active + platform-managed zone: Ingress only, no cert-manager
    annotation (the cloud cert manager owns the secret)."""
    CustomDomain.objects.create(
        registered_app=app,
        hostname="api.acme.com",
        validation_status=CustomDomain.ValidationStatus.VALIDATED,
        certificate_state=CustomDomain.CertificateState.ACTIVE,
        is_platform_managed_zone=True,
        is_active=True,
    )
    out = _render_app_ingresses_and_tls(deployment.pk, "ns", _make_manifest())
    assert len(out) == 1
    ing = out[0]
    assert ing["kind"] == "Ingress"
    assert ing["metadata"]["name"] == "hello-app-api-acme-com"
    assert ing["metadata"]["labels"]["astrolift.dev/cert-source"] == "auto"
    # Platform-managed: cluster's cert plumbing owns the Secret.
    assert ing["metadata"]["annotations"] == {}
    assert ing["spec"]["tls"][0] == {
        "hosts": ["api.acme.com"],
        "secretName": "hello-app-api-acme-com-tls",
    }
    rule = ing["spec"]["rules"][0]
    assert rule["host"] == "api.acme.com"
    backend = rule["http"]["paths"][0]["backend"]["service"]
    assert backend["name"] == "web"
    assert backend["port"]["number"] == 8080


@pytest.mark.django_db
def test_render_ingress_active_external_zone(app, deployment):
    """Active + external zone: Ingress carries cert-manager
    cluster-issuer annotation so HTTP-01 lands on first apply."""
    CustomDomain.objects.create(
        registered_app=app,
        hostname="api.acme.com",
        validation_status=CustomDomain.ValidationStatus.VALIDATED,
        certificate_state=CustomDomain.CertificateState.ACTIVE,
        is_platform_managed_zone=False,
        is_active=True,
    )
    out = _render_app_ingresses_and_tls(deployment.pk, "ns", _make_manifest())
    ing = next(r for r in out if r["kind"] == "Ingress")
    assert ing["metadata"]["annotations"] == {
        "cert-manager.io/cluster-issuer": "letsencrypt-prod",
    }


@pytest.mark.django_db
def test_render_ingress_byo_emits_tls_secret(app, deployment):
    """BYO: emits a kubernetes.io/tls Secret + an Ingress that
    references it. The Secret's data must be base64-encoded chain +
    key, split on the last END CERTIFICATE marker."""
    import base64

    CustomDomain.objects.create(
        registered_app=app,
        hostname="api.acme.com",
        validation_status=CustomDomain.ValidationStatus.VALIDATED,
        certificate_state=CustomDomain.CertificateState.BYO,
        byo_certificate_pem=PEM_CERT_OK + PEM_KEY_OK,
        is_active=True,
    )
    out = _render_app_ingresses_and_tls(deployment.pk, "ns", _make_manifest())
    kinds = sorted(r["kind"] for r in out)
    assert kinds == ["Ingress", "Secret"]

    secret = next(r for r in out if r["kind"] == "Secret")
    assert secret["type"] == "kubernetes.io/tls"
    assert secret["metadata"]["name"] == "hello-app-api-acme-com-tls"
    assert secret["metadata"]["labels"]["astrolift.dev/cert-source"] == "byo"
    tls_crt = base64.b64decode(secret["data"]["tls.crt"]).decode("utf-8")
    tls_key = base64.b64decode(secret["data"]["tls.key"]).decode("utf-8")
    assert "-----BEGIN CERTIFICATE-----" in tls_crt
    assert "-----END CERTIFICATE-----" in tls_crt
    assert "PRIVATE KEY" in tls_key
    # The split must NOT leak the key into the chain or vice versa.
    assert "PRIVATE KEY" not in tls_crt
    assert "BEGIN CERTIFICATE" not in tls_key

    ing = next(r for r in out if r["kind"] == "Ingress")
    assert ing["metadata"]["labels"]["astrolift.dev/cert-source"] == "byo"
    assert ing["spec"]["tls"][0]["secretName"] == "hello-app-api-acme-com-tls"


@pytest.mark.django_db
def test_render_skips_when_workload_has_no_port(app, deployment):
    """A manifest with no exposed port can't be routed to — no
    ingress is emitted regardless of the domain's cert state."""
    from astrolift_manifest.types import (
        ContainerManifest,
        NormalizedManifest,
        WorkloadManifest,
    )

    CustomDomain.objects.create(
        registered_app=app,
        hostname="api.acme.com",
        validation_status=CustomDomain.ValidationStatus.VALIDATED,
        certificate_state=CustomDomain.CertificateState.ACTIVE,
        is_active=True,
    )
    manifest_no_port = NormalizedManifest(
        name="hello-app",
        workloads=(
            WorkloadManifest(
                name="worker",
                kind="deployment",
                replicas=1,
                containers=(ContainerManifest(name="w", port=0, is_primary=True),),
            ),
        ),
        managed_services=(),
        defaults_applied=(),
        serialized={},
    )
    out = _render_app_ingresses_and_tls(deployment.pk, "ns", manifest_no_port)
    assert out == []


@pytest.mark.django_db
def test_render_skips_corrupt_byo_bundle(app, deployment):
    """BYO bundle missing the END CERTIFICATE marker can't be split
    into chain + key safely — skip rather than emit a broken Secret."""
    CustomDomain.objects.create(
        registered_app=app,
        hostname="api.acme.com",
        validation_status=CustomDomain.ValidationStatus.VALIDATED,
        certificate_state=CustomDomain.CertificateState.BYO,
        byo_certificate_pem="garbage with no end marker",
        is_active=True,
    )
    out = _render_app_ingresses_and_tls(deployment.pk, "ns", _make_manifest())
    assert out == []


# ---- pause / resume app ingress (#378) -----------------------------------


def _grant_deploy(resolver):
    resolver.grant(Permission.APP_DEPLOY)


@pytest.mark.django_db
def test_pause_app_ingress_flips_flag(
    env,
    fake_info,
    org,
    actor,
    permission_resolver,
):
    """Happy path: pauseAppIngress flips ``ingress_paused`` to True."""
    _grant_deploy(permission_resolver)
    assert env.ingress_paused is False
    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mutation.pause_app_ingress(
            info=fake_info,
            input=EnvironmentByIdInput(id=str(env.guid)),
        )
    assert result.ok is True
    env.refresh_from_db()
    assert env.ingress_paused is True
    # deploys_paused is an independent axis — pausing ingress must not
    # also pause deploys.
    assert env.deploys_paused is False


@pytest.mark.django_db
def test_pause_idempotent_when_already_paused(
    env,
    fake_info,
    org,
    actor,
    permission_resolver,
):
    """Re-firing pause on an already-paused env still returns ok and
    leaves the flag set — matches the pause_environment pattern."""
    _grant_deploy(permission_resolver)
    env.ingress_paused = True
    env.save(update_fields=["ingress_paused", "updated_at", "version"])
    prior_version = env.version
    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mutation.pause_app_ingress(
            info=fake_info,
            input=EnvironmentByIdInput(id=str(env.guid)),
        )
    assert result.ok is True
    env.refresh_from_db()
    assert env.ingress_paused is True
    # Idempotent: no-op writes must not bump optimistic-lock version.
    assert env.version == prior_version


@pytest.mark.django_db
def test_resume_app_ingress_flips_flag(
    env,
    fake_info,
    org,
    actor,
    permission_resolver,
):
    """Resume clears the pause flag."""
    _grant_deploy(permission_resolver)
    env.ingress_paused = True
    env.save(update_fields=["ingress_paused", "updated_at", "version"])
    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mutation.resume_app_ingress(
            info=fake_info,
            input=EnvironmentByIdInput(id=str(env.guid)),
        )
    assert result.ok is True
    env.refresh_from_db()
    assert env.ingress_paused is False


@pytest.mark.django_db
def test_pause_requires_app_deploy_permission(
    env,
    fake_info,
    org,
    actor,
    permission_resolver,
):
    """Without ``app.deploy`` the resolver denies — first line of the
    mutation body."""
    # No grant on permission_resolver — default-deny.
    mutation = LifecycleMutation()
    with _tenant_for(org, actor):
        result = mutation.pause_app_ingress(
            info=fake_info,
            input=EnvironmentByIdInput(id=str(env.guid)),
        )
    assert result.ok is False
    assert any(e.code == ErrorCode.PERMISSION_DENIED.value for e in result.errors)
    env.refresh_from_db()
    assert env.ingress_paused is False


@pytest.mark.django_db
def test_render_paused_emits_503_annotation(app, env, deployment):
    """env.ingress_paused=True → rendered Ingress carries the nginx
    server-snippet returning 503 AND the paused state label."""
    env.ingress_paused = True
    env.save(update_fields=["ingress_paused", "updated_at", "version"])
    # The deployment fixture already binds to ``env``; refresh the FK
    # cache so the renderer reads the new flag.
    deployment.app_environment = env
    CustomDomain.objects.create(
        registered_app=app,
        hostname="api.acme.com",
        validation_status=CustomDomain.ValidationStatus.VALIDATED,
        certificate_state=CustomDomain.CertificateState.ACTIVE,
        is_platform_managed_zone=True,
        is_active=True,
    )
    out = _render_app_ingresses_and_tls(deployment.pk, "ns", _make_manifest())
    ing = next(r for r in out if r["kind"] == "Ingress")
    assert (
        ing["metadata"]["annotations"].get("nginx.ingress.kubernetes.io/server-snippet")
        == 'return 503 "Astrolift: app is paused";'
    )
    assert ing["metadata"]["labels"]["astrolift.dev/ingress-state"] == "paused"


@pytest.mark.django_db
def test_render_live_omits_503_annotation(app, env, deployment):
    """env.ingress_paused=False (default) → no server-snippet, label
    flags the Ingress as live."""
    assert env.ingress_paused is False
    CustomDomain.objects.create(
        registered_app=app,
        hostname="api.acme.com",
        validation_status=CustomDomain.ValidationStatus.VALIDATED,
        certificate_state=CustomDomain.CertificateState.ACTIVE,
        is_platform_managed_zone=True,
        is_active=True,
    )
    out = _render_app_ingresses_and_tls(deployment.pk, "ns", _make_manifest())
    ing = next(r for r in out if r["kind"] == "Ingress")
    assert "nginx.ingress.kubernetes.io/server-snippet" not in ing["metadata"]["annotations"]
    assert ing["metadata"]["labels"]["astrolift.dev/ingress-state"] == "live"
