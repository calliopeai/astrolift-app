"""Install discovery uses the deployed HTTP transport before login."""

import hashlib
import json

import pytest
from django.conf import settings
from django.core.cache import cache

from config.schema import schema
from config.schema_public import schema_public

pytestmark = pytest.mark.django_db

QUERY = """query MobileInstallDiscovery {
  astroliftServerInfo {
    version apiVersion installId installLabel authMethods capabilities
    featureFlags { key enabled }
  }
}"""


def _post(client, query, **headers):
    return client.post(
        f"/{settings.BASE_URL}gql/config/public/",
        data=json.dumps({"query": query}),
        content_type="application/json",
        **headers,
    )


@pytest.mark.parametrize("debug", [False, True])
def test_cookieless_discovery_reaches_public_resolver_without_dev_autologin(client, settings, debug):
    settings.DEBUG = debug
    settings.DEFAULT_USER_TEST = "no-discovery-test-account"
    response = _post(client, QUERY)
    assert response.status_code == 200
    result = response.json()
    assert not result.get("errors")
    info = result["data"]["astroliftServerInfo"]
    expected = hashlib.sha256(schema.as_str().encode()).hexdigest()[:16]
    assert info["apiVersion"] == f"{info['version']}+{expected}"
    assert "server_info.handshake" in info["capabilities"]
    assert "sessionid" not in response.cookies


def test_public_schema_has_only_discovery_and_no_mutation_root(client):
    graphql_schema = schema_public._schema
    assert set(graphql_schema.query_type.fields) == {"astroliftServerInfo"}
    assert graphql_schema.mutation_type is None
    assert graphql_schema.subscription_type is None
    result = _post(client, "{ __schema { queryType { fields { name } } mutationType { name } } }").json()
    assert result["data"]["__schema"]["mutationType"] is None
    assert result["data"]["__schema"]["queryType"]["fields"] == [{"name": "astroliftServerInfo"}]


@pytest.mark.parametrize(
    "query",
    [
        "{ astroliftServerInfo { version } astroliftMyProfile { userId } }",
        "query { astroliftAppsPage(limit: 1) { totalCount } }",
        'mutation { redeployApp(input: {deploymentId: "absent"}) { ok } }',
        "query { ...Private } fragment Private on AstroliftServerInfoQuery { astroliftMyProfile { userId } }",
    ],
)
def test_public_transport_cannot_resolve_tenant_fields_or_mutations(client, query):
    response = _post(client, query)
    assert response.status_code in {200, 400}
    result = response.json()
    assert result.get("errors")
    assert not result.get("data")


def test_main_transport_still_requires_authentication(client, settings):
    settings.DEBUG = False
    response = client.post(
        f"/{settings.BASE_URL}gql/config/",
        data=json.dumps({"query": QUERY}),
        content_type="application/json",
    )
    assert response.status_code == 403


def test_discovery_remains_rate_limited_per_ip(client, settings):
    settings.RATELIMIT_ENABLE = True
    cache.clear()
    responses = [_post(client, "{ __typename }", REMOTE_ADDR="192.0.2.240") for _ in range(31)]
    assert all(response.status_code == 200 for response in responses[:30])
    assert responses[-1].status_code == 429
