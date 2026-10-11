"""Distinct verified identities must not share a managed configuration set."""

import re

import pytest

from aws.managed.email_ses import _safe, configuration_set_name


@pytest.mark.parametrize(
    "first,second",
    [
        ("ops@example.test", "ops-example.test"),
        ("ops+alerts@example.test", "ops-alerts@example.test"),
        ("a" * 50 + "@example.test", "a" * 50 + "-example.test"),
    ],
)
def test_distinct_identity_configuration_sets_preserve_legacy_secret_slug(first, second):
    # Secret references already use this lossy slug; changing it would orphan existing secrets.
    assert _safe(first) == _safe(second)
    names = [configuration_set_name(value) for value in (first, second)]
    assert names[0] != names[1]
    assert all(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) for name in names)
    assert names == [configuration_set_name(value) for value in (first, second)]
