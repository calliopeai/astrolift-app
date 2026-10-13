"""A signed-out or expired session on the GraphQL endpoint is a 401 the
clients understand, not a bare 403 page (CONFLICT, 2026-10-13)."""

import json

import pytest
from django.test import Client


@pytest.mark.django_db
def test_an_anonymous_graphql_call_answers_unauthenticated(settings):
    settings.DEBUG = False
    response = Client().post(
        "/app/gql/config/",
        json.dumps({"query": "{ __typename }"}),
        content_type="application/json",
        HTTP_X_PLATFORM="web",
    )
    assert response.status_code == 401
    assert response.json() == {
        "errors": [{"message": "Authentication required.", "extensions": {"code": "UNAUTHENTICATED"}}]
    }
