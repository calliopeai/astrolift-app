"""Authenticated HTTP helpers shared by domain diagnostic controls."""

from astrolift_identity.models import ApiToken


def http_token(w, *, scopes=("write:apps",), team=None, actor=None):
    from astrolift_identity.api_tokens import mint_token

    minted = mint_token()
    token = ApiToken.objects.create(
        user=actor or w.user,
        organization=w.org,
        team=team,
        name="actual HTTP",
        token_hash=minted.token_hash,
        scopes=list(scopes),
    )
    return token, {
        "HTTP_AUTHORIZATION": "Bearer " + minted.plaintext,
        "HTTP_X_ASTROLIFT_ORGANIZATION": str(w.org.guid),
    }


def graphql_http(client, headers, query, variables):
    import json

    reply = client.post(
        "/app/gql/config/",
        json.dumps({"query": query, "variables": variables}),
        content_type="application/json",
        **headers,
    )
    assert reply.status_code == 200, reply.status_code
    return reply.json()
