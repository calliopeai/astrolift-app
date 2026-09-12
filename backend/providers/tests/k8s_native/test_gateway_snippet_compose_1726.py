"""The gateway snippet composes; it does not own the annotation (#1726).

`configuration-snippet` is a shared nginx annotation. The first shape of
the gateway-secret feature rendered the whole key, so the reconcile
patched it wholesale -- and an app that had its own snippet content lost
it. On the SteadyMD install that content is what derives the per-request
identity headers one app's backend requires; without them the app
refuses every request with "An authenticated identity gateway is
required", which is the failure this feature exists to prevent.

The platform's contribution is fenced, so it can be replaced or removed
without touching anything else in the same annotation.
"""

from __future__ import annotations

from k8s_native.ingress import (
    PLATFORM_SNIPPET_BEGIN,
    PLATFORM_SNIPPET_END,
    OIDCAuthConfig,
    compose_configuration_snippet,
    nginx_auth_annotations,
    platform_gateway_snippet,
    strip_platform_snippet,
)

_SNIPPET_KEY = "nginx.ingress.kubernetes.io/configuration-snippet"

_APP_SNIPPET = (
    "auth_request_set $qsr_user $upstream_http_x_auth_request_user;\n"
    'proxy_set_header x-qsr-proxy-secret "app-owned";\n'
    "proxy_set_header x-qsr-user-id $qsr_user;"
)


def _auth(**kw):
    base = {"auth_proxy_host": "auth.example.com"}
    base.update(kw)
    return OIDCAuthConfig(**base)


def test_no_secret_contributes_no_block():
    assert platform_gateway_snippet(_auth()) == ""


def test_a_secret_contributes_a_fenced_block():
    block = platform_gateway_snippet(_auth(gateway_secret="s3cret"))
    assert block.startswith(PLATFORM_SNIPPET_BEGIN)
    assert block.endswith(PLATFORM_SNIPPET_END)
    assert 'proxy_set_header X-Astrolift-Gateway-Secret "s3cret";' in block


def test_the_apps_own_snippet_survives_the_platform_block():
    """The bug: the app's lines were replaced by the platform's."""

    out = compose_configuration_snippet(_APP_SNIPPET, platform_gateway_snippet(_auth(gateway_secret="s3cret")))
    assert "x-qsr-proxy-secret" in out
    assert "$qsr_user" in out
    assert "X-Astrolift-Gateway-Secret" in out


def test_removing_the_secret_removes_only_the_platform_block():
    with_both = compose_configuration_snippet(_APP_SNIPPET, platform_gateway_snippet(_auth(gateway_secret="s3cret")))

    after = compose_configuration_snippet(with_both, "")

    assert "x-qsr-proxy-secret" in after
    assert "X-Astrolift-Gateway-Secret" not in after
    assert PLATFORM_SNIPPET_BEGIN not in after


def test_rotating_the_secret_does_not_stack_blocks():
    once = compose_configuration_snippet(_APP_SNIPPET, platform_gateway_snippet(_auth(gateway_secret="old")))
    twice = compose_configuration_snippet(once, platform_gateway_snippet(_auth(gateway_secret="new")))

    assert twice.count(PLATFORM_SNIPPET_BEGIN) == 1
    assert "old" not in twice
    assert '"new"' in twice
    assert "x-qsr-proxy-secret" in twice


def test_nothing_left_clears_the_annotation():
    """An app that contributes nothing still loses the key when the
    secret goes away -- otherwise an empty annotation lingers."""

    only_platform = compose_configuration_snippet("", platform_gateway_snippet(_auth(gateway_secret="s3cret")))
    assert compose_configuration_snippet(only_platform, "") is None
    assert compose_configuration_snippet("", "") is None


def test_an_unterminated_fence_does_not_swallow_the_apps_lines():
    """Someone editing inside the fence must not cost the app its own
    content that sits before it."""

    hand_edited = f'{_APP_SNIPPET}\n{PLATFORM_SNIPPET_BEGIN}\nproxy_set_header X-Gone "x";'

    assert "x-qsr-proxy-secret" in strip_platform_snippet(hand_edited)
    assert "X-Gone" not in strip_platform_snippet(hand_edited)


def test_the_annotation_builder_threads_the_existing_snippet():
    annotations = nginx_auth_annotations(_auth(gateway_secret="s3cret"), existing_snippet=_APP_SNIPPET)
    assert "x-qsr-proxy-secret" in annotations[_SNIPPET_KEY]
    assert "X-Astrolift-Gateway-Secret" in annotations[_SNIPPET_KEY]


def test_the_annotation_builder_omits_the_key_when_there_is_nothing_to_say():
    assert _SNIPPET_KEY not in nginx_auth_annotations(_auth())
