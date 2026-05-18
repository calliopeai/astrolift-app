"""Edge security defaults applied across ingress drivers (#65).

Every IngressDriver renders manifests through render_ingress(); this
module returns the canonical security headers + TLS policy
annotations that Astrolift expects every variant to apply by default.

Defaults:
- TLS 1.3 minimum (TLS 1.2 OK for compat, but only via opt-in)
- HSTS with 1y max-age + includeSubDomains + preload
- X-Content-Type-Options: nosniff
- X-Frame-Options: DENY (clickjacking protection)
- Referrer-Policy: strict-origin-when-cross-origin
- WAF: managed common-rules ruleset on (off only via opt-out)

Drivers translate these abstract directives into per-controller
annotations / config (nginx-ingress configmap, ALB WebACL ARN, GCP
Cloud Armor policy, App Gateway WAF policy). The driver decides
which subset its controller supports + emits the matching
annotations.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TlsPolicy:
    min_version: str = "TLSv1.3"
    """TLSv1.2 | TLSv1.3 — recommend 1.3, 1.2 is the absolute floor."""

    cipher_preference: str = "modern"
    """modern | intermediate | legacy — Mozilla SSL config nomenclature."""

    require_sni: bool = True


@dataclass(frozen=True)
class HstsPolicy:
    enabled: bool = True
    max_age_seconds: int = 31_536_000  # 1 year
    include_subdomains: bool = True
    preload: bool = True


@dataclass(frozen=True)
class WafPolicy:
    enabled: bool = True
    managed_rules: tuple[str, ...] = (
        "common",
        "known_bad_inputs",
        "sql_injection",
        "xss",
    )
    rate_limit_requests_per_5min: int | None = None
    """When set, ingress applies a per-IP rate limit. None = no
    limit (controller's default)."""

    block_country_codes: tuple[str, ...] = ()
    """Geo-block when set. Empty = allow all."""


@dataclass(frozen=True)
class SecurityHeaders:
    x_content_type_options: str = "nosniff"
    x_frame_options: str = "DENY"
    referrer_policy: str = "strict-origin-when-cross-origin"
    content_security_policy: str | None = None
    """CSP is app-specific (per-app config). Driver emits when set."""

    permissions_policy: str | None = None


@dataclass(frozen=True)
class EdgeSecurityProfile:
    tls: TlsPolicy = field(default_factory=TlsPolicy)
    hsts: HstsPolicy = field(default_factory=HstsPolicy)
    waf: WafPolicy = field(default_factory=WafPolicy)
    headers: SecurityHeaders = field(default_factory=SecurityHeaders)

    def headers_dict(self) -> dict[str, str]:
        """Render the SecurityHeaders + HSTS into a flat name→value
        map. Drivers append these to their controller's
        configuration-snippet annotation."""
        out: dict[str, str] = {
            "X-Content-Type-Options": self.headers.x_content_type_options,
            "X-Frame-Options": self.headers.x_frame_options,
            "Referrer-Policy": self.headers.referrer_policy,
        }
        if self.hsts.enabled:
            directives = [f"max-age={self.hsts.max_age_seconds}"]
            if self.hsts.include_subdomains:
                directives.append("includeSubDomains")
            if self.hsts.preload:
                directives.append("preload")
            out["Strict-Transport-Security"] = "; ".join(directives)
        if self.headers.content_security_policy:
            out["Content-Security-Policy"] = (
                self.headers.content_security_policy
            )
        if self.headers.permissions_policy:
            out["Permissions-Policy"] = self.headers.permissions_policy
        return out


# Astrolift's recommended profile, applied by default if the
# tenant doesn't override.
DEFAULT_PROFILE = EdgeSecurityProfile()


def nginx_annotations(
    profile: EdgeSecurityProfile = DEFAULT_PROFILE,
) -> dict[str, str]:
    """Translate to nginx-ingress's annotation surface."""
    annotations: dict[str, str] = {
        "nginx.ingress.kubernetes.io/ssl-protocols": (
            "TLSv1.3 TLSv1.2"
            if profile.tls.min_version == "TLSv1.2"
            else "TLSv1.3"
        ),
        "nginx.ingress.kubernetes.io/ssl-prefer-server-ciphers": "true",
    }
    snippet_lines = []
    for header, value in profile.headers_dict().items():
        snippet_lines.append(
            f'more_set_headers "{header}: {value}";',
        )
    if snippet_lines:
        annotations[
            "nginx.ingress.kubernetes.io/configuration-snippet"
        ] = "\n".join(snippet_lines)
    if profile.waf.rate_limit_requests_per_5min is not None:
        # nginx rate-limit per source IP
        rps = max(
            1,
            profile.waf.rate_limit_requests_per_5min // 300,
        )
        annotations[
            "nginx.ingress.kubernetes.io/limit-rps"
        ] = str(rps)
    return annotations


def alb_annotations(
    profile: EdgeSecurityProfile = DEFAULT_PROFILE,
) -> dict[str, str]:
    """Translate to AWS Load Balancer Controller's annotation surface."""
    out: dict[str, str] = {
        "alb.ingress.kubernetes.io/ssl-policy": (
            "ELBSecurityPolicy-TLS13-1-2-2021-06"
            if profile.tls.min_version == "TLSv1.3"
            else "ELBSecurityPolicy-TLS-1-2-2017-01"
        ),
    }
    if profile.waf.enabled:
        # Operator pre-creates the WebACL; driver references it.
        out["alb.ingress.kubernetes.io/wafv2-acl-arn"] = (
            "AUTO_RESOLVE"
        )
    return out


def gcp_annotations(
    profile: EdgeSecurityProfile = DEFAULT_PROFILE,
) -> dict[str, str]:
    """Translate to GKE Ingress / Gateway annotation surface."""
    out: dict[str, str] = {
        "networking.gke.io/v1beta1.FrontendConfig": (
            "AUTO_RESOLVE"
        ),
    }
    if profile.waf.enabled:
        # Cloud Armor policy attached via BackendConfig — operator
        # pre-creates the policy.
        out[
            "cloud.google.com/backend-config"
        ] = '{"default": "astrolift-default"}'
    return out


def appgw_annotations(
    profile: EdgeSecurityProfile = DEFAULT_PROFILE,
) -> dict[str, str]:
    """Translate to AGIC's annotation surface."""
    out: dict[str, str] = {}
    if profile.tls.min_version == "TLSv1.3":
        out[
            "appgw.ingress.kubernetes.io/ssl-redirect"
        ] = "true"
    if profile.waf.enabled:
        # App Gateway has WAF v2 attached at gateway level —
        # operator pre-configures.
        out["appgw.ingress.kubernetes.io/waf-policy-for-path"] = (
            "AUTO_RESOLVE"
        )
    return out
