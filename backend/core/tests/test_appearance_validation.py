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
