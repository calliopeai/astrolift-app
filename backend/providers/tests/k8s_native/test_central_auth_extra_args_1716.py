"""The proxy's flags have an operator seam (#1716).

`extra_args` was a closed dict, so anything an operator had to set for
their provider was unreachable without hand-editing the rendered
HelmRelease. The flag that surfaced it:
`insecure-oidc-allow-unverified-email`. oauth2-proxy rejects a login
whose id_token does not assert `email_verified`, with a 500 at the
callback and a healthy `/ping` throughout, so nothing alerts.

Where the broker *can* assert the claim, fixing it in the broker's
attribute mapping is the right answer and astrolift needs no change.
The gap is the case where the upstream cannot -- a SAML federation
emitting no equivalent, on a tenant the cluster operator does not
control. Then the proxy flag is the only lever there is.

A general seam rather than one named boolean: the same gap covers
`email-domain`, hardcoded to `*` so any address the IdP returns is
authorized, and every provider-specific flag after it.
"""

from __future__ import annotations

from k8s_native.central_auth import central_auth_component, proxy_extra_args

_BASE = {
    "auth_proxy_host": "auth.example.com",
    "client_id": "abc",
    "discovery_url": "https://idp.example.com/.well-known/openid-configuration",
    "cookie_secret": "s" * 32,
}


def _args(config):
    return central_auth_component(config).helm_values["extraArgs"]


def test_no_overrides_leaves_the_defaults_alone():
    args = _args(dict(_BASE))
    assert args["provider"] == "oidc"
    assert args["email-domain"] == "*"
    assert "insecure-oidc-allow-unverified-email" not in args


def test_an_override_reaches_the_rendered_flags():
    """The reported case: the only lever for a federation whose upstream
    cannot assert email_verified."""

    args = _args({**_BASE, "proxy_extra_args": {"insecure-oidc-allow-unverified-email": True}})
    assert args["insecure-oidc-allow-unverified-email"] == "true"


def test_an_override_can_narrow_the_email_domain():
    """The same missing seam, and the reason this is a dict rather than
    one boolean."""

    args = _args({**_BASE, "proxy_extra_args": {"email-domain": "steadymd.com"}})
    assert args["email-domain"] == "steadymd.com"


def test_leading_dashes_are_tolerated():
    """The chart's extraArgs map takes bare flag names; an operator
    copying from a command line will write --flag."""

    assert proxy_extra_args({"proxy_extra_args": {"--foo": "bar"}}) == {"foo": "bar"}


def test_booleans_render_as_helm_strings():
    """helm puts these on a command line; a YAML bool arrives as `True`."""

    out = proxy_extra_args({"proxy_extra_args": {"a": True, "b": False}})
    assert out == {"a": "true", "b": "false"}


def test_a_null_value_is_dropped_rather_than_rendered():
    assert proxy_extra_args({"proxy_extra_args": {"a": None}}) == {}


def test_a_non_dict_override_is_ignored():
    """A misconfigured value must not take the auth host down."""

    assert proxy_extra_args({"proxy_extra_args": "insecure-oidc-allow-unverified-email"}) == {}
    assert proxy_extra_args({}) == {}
    assert proxy_extra_args(None) == {}


def test_an_override_can_replace_a_computed_flag():
    """Computed flags are computed for a reason, but an operator who
    knows their provider better should not have to hand-edit a rendered
    HelmRelease to say so."""

    args = _args({**_BASE, "proxy_extra_args": {"whitelist-domain": ".other.example"}})
    assert args["whitelist-domain"] == ".other.example"


def test_overrides_do_not_enable_an_unconfigured_component():
    """Flags are not configuration. A cluster with no client id still has
    no auth host."""

    component = central_auth_component({"proxy_extra_args": {"email-domain": "x.com"}})
    assert component.default_enabled is False
