import jwt
import pytest

from core.services.client_cove_support import ClientCoveSupportClient


class _User:
    pk = 7
    email = "operator@example.test"


class _Org:
    slug = "conflict"


def test_support_client_is_disabled_by_default(settings):
    settings.ASTROLIFT_SUPPORT_ENABLED = False
    with pytest.raises(RuntimeError, match="disabled"):
        ClientCoveSupportClient(user=_User(), organization=_Org())


def test_support_client_mints_short_lived_scoped_assertion(settings):
    settings.ASTROLIFT_SUPPORT_ENABLED = True
    settings.CLIENT_COVE_SUPPORT_URL = "https://support.example.test"
    settings.CLIENT_COVE_SUPPORT_API_KEY = "shared-secret"
    client = ClientCoveSupportClient(user=_User(), organization=_Org())
    claims = jwt.decode(
        client.headers["X-Astrolift-Support-Assertion"],
        "shared-secret",
        algorithms=["HS256"],
        audience="client-cove-support",
        issuer="astrolift",
    )
    assert claims["email"] == _User.email
    assert claims["org"] == _Org.slug
    assert claims["sub"] == str(_User.pk)
    assert set(claims) >= {"exp", "iat", "jti", "sub", "email", "org"}
