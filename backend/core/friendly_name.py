"""Friendly-name generator — memorable app names like
``exciting-talkative-platypus`` so a registered/bootstrapped app never has to
start nameless. Words are chosen so ``adjective-adjective-animal`` stays inside
the app slug contract (``^[a-z0-9-]{1,40}$``); an over-long combo falls back to
``adjective-animal``. Mirrors ``frontend/lib/friendly-name.ts``.
"""

from __future__ import annotations

import secrets

ADJECTIVES = (
    "eager",
    "brave",
    "calm",
    "clever",
    "bold",
    "bright",
    "swift",
    "quiet",
    "lively",
    "gentle",
    "keen",
    "witty",
    "sunny",
    "merry",
    "nimble",
    "chill",
    "cosmic",
    "electric",
    "mellow",
    "plucky",
    "quirky",
    "snappy",
    "spry",
    "sturdy",
    "zesty",
    "breezy",
    "chirpy",
    "dapper",
    "exciting",
    "talkative",
    "curious",
    "fuzzy",
    "jolly",
    "peppy",
    "rustic",
    "shiny",
    "smooth",
    "vivid",
)

ANIMALS = (
    "platypus",
    "otter",
    "falcon",
    "badger",
    "lemur",
    "panda",
    "koala",
    "gecko",
    "narwhal",
    "walrus",
    "puffin",
    "ferret",
    "beaver",
    "marmot",
    "raccoon",
    "meerkat",
    "wombat",
    "ocelot",
    "tapir",
    "heron",
    "lynx",
    "moose",
    "bison",
    "cobra",
    "finch",
    "gopher",
    "iguana",
    "jackal",
    "manatee",
    "newt",
    "quokka",
    "raven",
    "seal",
    "toucan",
    "vole",
    "yak",
    "zebra",
    "mantis",
)

SLUG_MAX = 40


def generate_friendly_slug() -> str:
    """Return a random URL-safe slug, e.g. ``exciting-talkative-platypus``.

    Guaranteed to satisfy ``^[a-z0-9-]{1,40}$``; drops to ``adjective-animal``
    if the three-word form would exceed ``SLUG_MAX`` chars.
    """
    animal = secrets.choice(ANIMALS)
    adj1 = secrets.choice(ADJECTIVES)
    adj2 = secrets.choice(ADJECTIVES)
    # Avoid the occasional ``swift-swift-otter``.
    if adj2 == adj1:
        adj2 = secrets.choice(ADJECTIVES)

    three = f"{adj1}-{adj2}-{animal}"
    if len(three) <= SLUG_MAX:
        return three
    two = f"{adj1}-{animal}"
    return two if len(two) <= SLUG_MAX else animal


def friendly_name_from_slug(slug: str) -> str:
    """Title-cased display form of a hyphenated slug.

    e.g. ``exciting-talkative-platypus`` -> ``Exciting Talkative Platypus``.
    """
    return " ".join(part.capitalize() for part in slug.split("-") if part)
