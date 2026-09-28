"""Server-side validation of the appearance axes (#135).

The org-level house theme is persisted, so the axis vocabulary has to be
enforced here rather than trusted from the client. These pin the two things
that matter: a valid partial preference survives untouched, and anything else
is refused with a message that names what was allowed — never dropped
silently, which is the failure mode the whole issue exists to stop.
"""

from __future__ import annotations

import pytest

from core.appearance import AppearanceError, validate_appearance


def test_empty_is_valid_and_means_no_house_theme():
    assert validate_appearance({}) == {}
    assert validate_appearance(None) == {}


def test_partial_preference_is_allowed():
    """An org may pin one axis and leave the rest to each person."""
    assert validate_appearance({"accent": "copper"}) == {"accent": "copper"}


def test_full_preference_round_trips():
    payload = {"ground": "black", "accent": "green", "density": "compact", "corners": 2}
    assert validate_appearance(payload) == payload


@pytest.mark.parametrize(
    "payload",
    [
        {"accent": "purple"},  # a hue the brand forbids outright
        {"ground": "neon"},
        {"density": "roomy"},
        {"corners": 7},
    ],
)
def test_unknown_values_are_refused(payload):
    with pytest.raises(AppearanceError) as exc:
        validate_appearance(payload)
    # the message has to say what was allowed, or the operator is guessing
    assert "allowed:" in str(exc.value)


def test_unknown_keys_are_refused_not_dropped():
    """Silently dropping an axis is how "I set it and nothing happened"
    happens — the exact class of bug this work came out of."""
    with pytest.raises(AppearanceError, match="unknown appearance key"):
        validate_appearance({"colour_scheme": "black"})


def test_true_is_not_accepted_as_corners_one():
    """bool subclasses int, so True would otherwise pass an `in {0,2,4,10}`
    membership test against 1 if 1 were ever allowed — and reads as a bug
    either way."""
    with pytest.raises(AppearanceError, match="not a boolean"):
        validate_appearance({"corners": True})


def test_non_mapping_is_refused():
    with pytest.raises(AppearanceError, match="must be an object"):
        validate_appearance("black")


def test_custom_accent_is_allowed_as_lowercase_hex():
    """Palette option A: any colour an org picks, stored as #rrggbb."""
    assert validate_appearance({"accent": "#3fa7d6"}) == {"accent": "#3fa7d6"}


@pytest.mark.parametrize("bad", ["#3FA7D6", "3fa7d6", "#3fa7d", "#3fa7d6ff", "blue"])
def test_malformed_custom_accent_is_refused(bad):
    with pytest.raises(AppearanceError, match="#rrggbb"):
        validate_appearance({"accent": bad})


def test_custom_accent_must_clear_contrast_on_a_pinned_ground():
    """A near-black accent on the black ground would vanish; refuse it."""
    with pytest.raises(AppearanceError, match="3:1"):
        validate_appearance({"ground": "black", "accent": "#111111"})
    assert validate_appearance({"ground": "black", "accent": "#8fd82a"}) == {
        "ground": "black",
        "accent": "#8fd82a",
    }


def test_custom_accent_without_a_ground_is_checked_by_the_client():
    """No ground pinned: each person's ground varies, so the client re-checks."""
    assert validate_appearance({"accent": "#111111"}) == {"accent": "#111111"}
