"""A one-off box against a stock image, with no durable spec (cli#84).

`astro box ensure` was idempotent on the durable object behind it, so
attaching a throwaway agent host to a stock image meant upserting an
`AgentEnvironmentSpec` you did not want to keep, then cleaning it up or
leaving it as litter. A box is already one-off; the spec was not.

The load-bearing tests are the idempotency ones. `ensure` exists so the org
does not grow a node per press of the IDE button, and the slug is what
enforces that -- it is a pure function of the request, and
`(organization, slug)` is unique among live rows, so two concurrent presses
collide on the constraint rather than both succeeding.

Image participates in that identity: same image is the same request and
attaches; a different image is a different request. A node per *distinct
image* is inherent to having asked for a different image, which is not the
failure the verb guards against.
"""

from __future__ import annotations

import pytest

from astrolift_agents.services.agent_box import AgentBoxEnsureError, box_slug_for

IMG_A = "ghcr.io/acme/dev:v1"
IMG_B = "ghcr.io/acme/dev:v2"


# ---- the slug is the identity -------------------------------------------


def test_the_same_image_yields_the_same_slug():
    """Two presses, one box."""
    first = box_slug_for(environment_spec=None, agent=None, owner_id=7, image=IMG_A)
    second = box_slug_for(environment_spec=None, agent=None, owner_id=7, image=IMG_A)

    assert first == second


def test_a_different_image_yields_a_different_slug():
    a = box_slug_for(environment_spec=None, agent=None, owner_id=7, image=IMG_A)
    b = box_slug_for(environment_spec=None, agent=None, owner_id=7, image=IMG_B)

    assert a != b


def test_two_people_on_the_same_image_do_not_share_a_box():
    """Same rule the spec path already has: the owner is in the slug."""
    mine = box_slug_for(environment_spec=None, agent=None, owner_id=7, image=IMG_A)
    theirs = box_slug_for(environment_spec=None, agent=None, owner_id=8, image=IMG_A)

    assert mine != theirs


def test_images_sharing_a_prefix_do_not_collide():
    """Why the image is hashed rather than embedded. A truncated reference
    would attach a caller to the wrong box, and the two differ only past the
    point a slug could hold."""
    long_a = "ghcr.io/acme/a-very-long-organisation-name/service:sha-aaaaaaaaaaaa"
    long_b = "ghcr.io/acme/a-very-long-organisation-name/service:sha-bbbbbbbbbbbb"

    assert box_slug_for(environment_spec=None, agent=None, owner_id=1, image=long_a) != box_slug_for(
        environment_spec=None, agent=None, owner_id=1, image=long_b
    )


def test_whitespace_does_not_make_a_second_box():
    a = box_slug_for(environment_spec=None, agent=None, owner_id=1, image=IMG_A)
    b = box_slug_for(environment_spec=None, agent=None, owner_id=1, image=f"  {IMG_A}  ")

    assert a == b


def test_the_slug_stays_within_the_column():
    slug = box_slug_for(environment_spec=None, agent=None, owner_id=1, image="x" * 4000)

    assert len(slug) <= 200


def test_an_image_only_box_is_still_addressable():
    """No agent and no spec, so the image is the only thing naming it. The
    slug still has to read as a box rather than as a bare hash."""
    slug = box_slug_for(environment_spec=None, agent=None, owner_id=1, image=IMG_A)

    assert slug.startswith("box-")


# ---- the mutual exclusion -----------------------------------------------


@pytest.mark.django_db
def test_a_spec_and_an_image_together_are_refused(db):
    """Mutually exclusive by construction, not preference: a spec already
    names an image, and silently preferring one would make the box's contents
    depend on which field the caller happened to send."""
    from astrolift_agents.services.agent_box import ensure_agent_box
    from astrolift_identity.models import Organization

    org = Organization.objects.create(name="Acme", slug="acme")

    with pytest.raises(AgentBoxEnsureError) as exc:
        ensure_agent_box(organization=org, environment_spec_slug="dev", image=IMG_A)

    assert exc.value.field == "image"
    assert "not both" in exc.value.message


@pytest.mark.django_db
def test_nothing_at_all_is_still_refused(db):
    """The pre-existing guard, with the message widened to mention images."""
    from astrolift_agents.services.agent_box import ensure_agent_box
    from astrolift_identity.models import Organization

    org = Organization.objects.create(name="Acme", slug="acme")

    with pytest.raises(AgentBoxEnsureError) as exc:
        ensure_agent_box(organization=org)

    assert "image" in exc.value.message


# ---- the API surface ----------------------------------------------------


def test_the_mutation_input_accepts_an_image():
    from astrolift_agents.schema.mutations import EnsureAgentBoxInput

    assert "image" in EnsureAgentBoxInput.__annotations__
