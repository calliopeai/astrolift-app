"""Public preview discovery describes contracts without granting access."""

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory

from config.schema import schema
from core.schema.context import StrawberryContext

pytestmark = pytest.mark.django_db


def context():
    request = RequestFactory().get("/app/gql/config/")
    request.user = AnonymousUser()
    request.session = {}
    return StrawberryContext(request)


def test_preview_identity_and_cost_controls_are_discoverable_but_not_authority():
    public = schema.execute_sync("{ astroliftServerInfo { capabilities } }", context_value=context())
    assert not public.errors
    assert {"previews.exact_identity", "previews.reviewed_routes", "previews.explicit_runtime_cost"} <= set(
        public.data["astroliftServerInfo"]["capabilities"]
    )
    refused = schema.execute_sync(
        """{
      astroliftPreviewEnvironment(id: "11111111-1111-4111-8111-111111111111", includeRuntimeCost: true) {
        id environmentStatus runtimeStatus environment { environmentId }
      }
    }""",
        context_value=context(),
    )
    assert refused.errors
    assert refused.data["astroliftPreviewEnvironment"] is None
