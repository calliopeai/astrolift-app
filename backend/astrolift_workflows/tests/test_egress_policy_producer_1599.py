"""An app can declare egress, and one that has not is untouched (#1599).

`render_network_policy` has built a complete deny-by-default NetworkPolicy
since the feature was specced -- ingress only from the ingress-controller
namespace, egress only to cluster DNS, bound managed services, the app's
allow-listed FQDNs and CIDRs -- and `astrolift_clusters/egress.py` renders
the allow/deny lists it consumes. `EgressPolicy` was instantiated nowhere
outside tests, because nothing stored per-app egress rules.

The most important tests here are the ones asserting that **nothing is
emitted**. The issue names the hazard directly: emitting a NetworkPolicy for
an app that never had one changes live network posture, and the policy is
deny-by-default, so it silently cuts every egress nobody thought to declare.
The symptom would be a production app that can no longer reach a third-party
API it has always reached.
"""

from __future__ import annotations

from astrolift_workflows.activities.network_policy import (
    egress_policy_for_app,
    render_app_network_policy,
)


class _App:
    slug = "hello"

    def __init__(self, policy=None):
        self.network_policy = policy or {}


# ---- the default: emit nothing ------------------------------------------


def test_an_app_with_no_declaration_gets_no_policy():
    """Every app today. This is the case that must not change."""
    assert egress_policy_for_app(_App()) is None
    assert render_app_network_policy(_App(), namespace="acme-hello") == []


def test_declaring_rules_without_enabling_emits_nothing():
    """An operator writing the rules out is not the same as switching the
    posture on, and the write should be safe to do first."""
    app = _App({"allowed_cidrs": ["10.0.0.0/8"]})

    assert egress_policy_for_app(app) is None


def test_a_non_dict_declaration_emits_nothing():
    for junk in ("", [], 7, None):
        assert egress_policy_for_app(_App(junk)) is None


# ---- opted in -----------------------------------------------------------


def test_enabling_produces_a_policy():
    app = _App({"enabled": True, "allowed_cidrs": ["10.0.0.0/8"]})

    policy = egress_policy_for_app(app)

    assert policy is not None
    assert policy.allowed_cidrs == ("10.0.0.0/8",)


def test_it_renders_a_networkpolicy_for_the_app():
    app = _App({"enabled": True, "allowed_fqdns": ["api.stripe.com"]})

    resources = render_app_network_policy(app, namespace="acme-hello")

    assert len(resources) == 1
    assert resources[0]["kind"] == "NetworkPolicy"
    assert resources[0]["metadata"]["namespace"] == "acme-hello"


def test_a_bare_string_is_accepted_where_a_list_belongs():
    """Hand-edited JSON columns get scalars where lists are meant. Splitting
    "10.0.0.0/8" into characters would produce a policy of nonsense CIDRs
    rather than an error."""
    app = _App({"enabled": True, "allowed_cidrs": "10.0.0.0/8"})

    assert egress_policy_for_app(app).allowed_cidrs == ("10.0.0.0/8",)


# ---- malformed input must not produce a partial allow-list ---------------


def test_an_invalid_cidr_emits_nothing_rather_than_a_partial_policy():
    """The subtle one. Half an allow-list is not a safer allow-list -- it is
    a deny-list of everything somebody meant to permit. `EgressPolicy`
    validates on construction, so this refuses the whole thing.
    """
    app = _App({"enabled": True, "allowed_cidrs": ["10.0.0.0/8", "not-a-cidr"]})

    assert egress_policy_for_app(app) is None
    assert render_app_network_policy(app, namespace="acme-hello") == []


def test_an_invalid_fqdn_emits_nothing():
    app = _App({"enabled": True, "allowed_fqdns": ["https://api.stripe.com/v1"]})

    assert egress_policy_for_app(app) is None


def test_an_unknown_source_ip_mode_falls_back_rather_than_failing():
    """A typo in an optional mode should not cost the app its whole policy --
    unlike a bad CIDR, the default here is a valid, safe answer."""
    from astrolift_clusters.egress import SourceIPMode

    app = _App({"enabled": True, "source_ip_mode": "teleportation"})

    policy = egress_policy_for_app(app)

    assert policy is not None
    assert policy.source_ip_mode is SourceIPMode.DEFAULT
