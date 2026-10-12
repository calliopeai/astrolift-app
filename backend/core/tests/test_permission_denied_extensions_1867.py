"""A refused permission carries the same code everywhere (#1867).

GraphQL surfaced ``PermissionDenied`` as a bare message; the CLI reads
``extensions.code`` and ``extensions.reason`` and found neither.
"""

from __future__ import annotations

import uuid

import pytest

from astrolift_clusters.tests.domain_http_helpers_2287 import graphql_http, http_token
from astrolift_identity.models import Member
from core.tests.utils.scope_world import ScopeWorld, make_user

pytestmark = pytest.mark.django_db(transaction=True)


def test_a_refused_read_says_permission_denied_with_its_reason_and_permission(client):
    world = ScopeWorld("pde-" + uuid.uuid4().hex[:8])
    roleless = make_user("r" + uuid.uuid4().hex[:6])
    Member.objects.create(user=roleless, scope_kind="ORG", scope_id=world.org.pk)
    _token, headers = http_token(world, scopes=("admin",), actor=roleless)

    reply = graphql_http(client, headers, "{astroliftActionSafetyRules{action}}", {})

    [error] = reply["errors"]
    assert error["extensions"]["code"] == "PERMISSION_DENIED"
    assert error["extensions"]["permission"] == "org.read"
    assert error["extensions"]["reason"] and error["extensions"]["reason"] in error["message"]
