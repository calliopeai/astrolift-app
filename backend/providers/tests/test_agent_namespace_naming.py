"""Agent namespaces stay valid for the org slugs the platform actually allows (#1379).

Organization slugs allow 200 characters. A Kubernetes namespace allows 63. Five
runtime paths built `astrolift-agents-<org-slug>` by interpolation, so a valid
organization produced an invalid namespace and agent dispatch or managed-service
provisioning failed on a name.

The subtler half is agreement. Two of those call sites are NetworkPolicy
namespace selectors in managed-service drivers, and a selector derived
differently from the namespace it targets matches nothing. That surfaces as a
connectivity problem, days later, nowhere near the naming code.
"""

from __future__ import annotations

import pytest

from _sdk.k8s_naming import AGENT_NAMESPACE_PREFIX, agent_namespace, dns_label

#: Kubernetes namespace / label-value ceiling.
MAX = 63


def _valid_dns_label(value: str) -> bool:
    import re

    return bool(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", value)) and len(value) <= MAX


# ---- boundaries --------------------------------------------------------------


@pytest.mark.parametrize("length", [1, 40, 46, 47, 62, 63, 64, 100, 200])
def test_any_allowed_slug_length_yields_a_valid_namespace(length):
    """200 is the slug ceiling; 46 is the longest that still fits unmodified."""
    assert _valid_dns_label(agent_namespace("a" * length))


def test_the_longest_unmodified_slug_is_left_alone():
    """Everything at or under this length is byte-for-byte what the old
    f-string produced, which is why nothing deployed needs migrating."""
    slug = "a" * 46

    assert agent_namespace(slug) == f"astrolift-agents-{slug}"
    assert len(agent_namespace(slug)) == MAX


def test_one_character_past_the_boundary_is_truncated_and_still_valid():
    slug = "a" * 47

    result = agent_namespace(slug)
    assert result != f"astrolift-agents-{slug}"
    assert _valid_dns_label(result)


def test_names_that_change_are_only_names_that_could_not_have_worked():
    """The migration argument. If a slug produced a valid namespace before, it
    produces the same one now."""
    for length in range(1, 47):
        slug = "x" * length
        naive = f"astrolift-agents-{slug}"
        if len(naive) <= MAX:
            assert agent_namespace(slug) == naive, length


# ---- collisions --------------------------------------------------------------


def test_two_long_slugs_sharing_a_prefix_do_not_collapse():
    """Plain truncation would map every slug with the same first 46 characters
    onto one namespace, and two organizations would share agent workloads."""
    a = "org-" + "a" * 196
    b = "org-" + "a" * 195 + "b"

    assert agent_namespace(a) != agent_namespace(b)


def test_the_suffix_is_stable_across_calls():
    """A namespace recomputed on a later dispatch must be the same one."""
    slug = "z" * 120

    assert agent_namespace(slug) == agent_namespace(slug)


def test_unsafe_characters_do_not_let_two_orgs_meet():
    """Folding characters is lossy: `a.b` and `a-b` normalize alike. The hash
    is what keeps them apart."""
    assert agent_namespace("acme.corp") != agent_namespace("acme-corp")


def test_case_differences_do_not_collapse_silently():
    """Namespaces are lowercase, so casing is folded, and the suffix records
    that identity changed rather than pretending it did not."""
    assert agent_namespace("AcmeCorp") != agent_namespace("acmecorp")


# ---- agreement across call sites ---------------------------------------------


def test_every_call_site_derives_the_same_name():
    """The NetworkPolicy failure mode. Selectors are built in the provider
    subtree and the namespace is created in the app; both go through here.

    Skipped in the providers job, which runs standalone and deliberately does
    not install the app's dependencies. It runs in the app shards, which is
    where both sides of the boundary are importable at once.
    """
    pytest.importorskip("temporalio", reason="providers tests run without the app installed")
    from astrolift_workflows.activities import agent_stage

    for slug in ("acme", "a" * 46, "a" * 47, "a" * 200, "Weird_Org.Name"):
        assert agent_stage._agent_namespace(slug) == agent_namespace(slug), slug


def test_the_prefix_constant_matches_the_derived_name():
    """A caller composing the prefix by hand must land in the same place."""
    assert agent_namespace("acme") == f"{AGENT_NAMESPACE_PREFIX}-acme"


def test_the_helper_is_the_generic_one_with_the_prefix_applied():
    assert agent_namespace("acme") == dns_label(AGENT_NAMESPACE_PREFIX, "acme")


# ---- refusals -----------------------------------------------------------------


def test_an_empty_slug_is_not_silently_the_shared_prefix():
    """Returning the bare prefix would put every org with a missing slug into
    one namespace together."""
    assert agent_namespace("") == AGENT_NAMESPACE_PREFIX


@pytest.mark.parametrize("slug", ["!!!", "...", "???"])
def test_a_slug_of_only_unsafe_characters_still_gets_its_own_namespace(slug):
    """Every character folds away, so the normalized name is just the prefix.
    The hash is what stops three such organizations sharing one namespace with
    each other and with the no-slug case."""
    result = agent_namespace(slug)

    assert _valid_dns_label(result)
    assert result != AGENT_NAMESPACE_PREFIX


def test_unsafe_slugs_do_not_collide_with_each_other():
    assert len({agent_namespace(s) for s in ("!!!", "...", "???")}) == 3
