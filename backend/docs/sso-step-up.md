# Browser SSO step-up

SSO step-up reuses the configured OpenID Connect provider. Register the callback
URL returned by Django's `auth1-elevate-sso-callback` route with that provider.
`AUTH0_DOMAIN`, `AUTH0_CLIENT_ID`, `AUTH0_CLIENT_SECRET`,
`AUTH0_CLIENT_SCOPES` (including `openid`) and optional
`AUTH0_SERVER_METADATA_URL` configure the existing Authlib registration.
Use the deployed HTTPS API origin and route prefix, rather than a frontend-only
origin or a localhost redirect. This flow uses persisted Django database sessions.

Preconditions: an operator who can set the webservice environment
(`AUTH0_DOMAIN`, `AUTH0_CLIENT_ID`, `AUTH0_CLIENT_SECRET`, `AUTH0_CLIENT_SCOPES`,
`AUTH0_SERVER_METADATA_URL`, `STEP_UP_SSO_FRESHNESS_SECONDS`,
`STEP_UP_AUTH_TTL_SECONDS`, `STEP_UP_AUTH_MAX_TTL_SECONDS`; values never in git)
and an administrator of the OpenID Connect provider to register the callback;
an authenticated browser session whose signed-in user already has an
`auth1.UserInfo` link to that provider; the routes are `auth1/elevate-sso/`
(`auth1-elevate-sso-start`) and `auth1/elevate-sso/callback/`
(`auth1-elevate-sso-callback`), mounted under `BASE_URL` (the `/app/`
prefix). Placeholders: `<api-origin>` is the deployed HTTPS API origin;
`<return-path>` is a relative console path.

An authenticated browser opens `auth1-elevate-sso-start` with a safe relative
`return` path. Astrolift requests `prompt=login`, `max_age=0` and a one-shot state
and nonce. The provider must return a signed ID token with a fresh `auth_time`
and the requested nonce. Authlib validates the configured issuer, signature and
audience; an unverified token response or user-info JSON is not proof.

The verified issuer and subject must identify an existing `auth1.UserInfo` link
to the same active internal actor. Matching email addresses do not establish
identity. The browser session must still authenticate that actor using Django's
current backend and session authentication hash, with the same persisted session
key and ceremony binding. Concurrent logout, expiry, password changes, account
changes and a removed or reassigned identity link prevent elevation.

`STEP_UP_SSO_FRESHNESS_SECONDS` defaults to 60 seconds. Freshness is checked after
all admission locks, so time spent waiting does not extend a proof's validity.
`STEP_UP_AUTH_TTL_SECONDS` and `STEP_UP_AUTH_MAX_TTL_SECONDS` retain the existing
bounded elevation lifetime. Final persistence uses the latest locked session bag;
response middleware cannot repeat a stale authentication write, including when
`SESSION_SAVE_EVERY_REQUEST` is enabled. A denial consumes only its matching
ceremony and preserves a newer flow.

A successful callback returns to the relative path. Refusals add a short `stepUp`
code such as `session_mismatch`, `identity_mismatch`, `stale_auth_time` or
`token_exchange_failed`. Start a new ceremony after a refusal. In-flight ceremonies
created before actor/session binding was introduced, or with a changed session key
(including authentication-hash fallback rotation), must be restarted. Provider
response bodies, raw claims and exception messages are omitted from the step-up
control-plane diagnostics.

This is a browser-session proof, not general action approval, an API bearer proof,
a new login/linking flow or an implementation of WebAuthn, OTP or mobile consent.
The broader action-safety requirements and durable audit contract remain separate.
## Verify

```bash
# Register this URL with the provider, then open the start URL in a signed-in browser
echo "https://<api-origin>/app/auth1/elevate-sso/callback/"
open "https://<api-origin>/app/auth1/elevate-sso/?return=<return-path>"
pytest astrolift_identity/tests/test_step_up_sso.py astrolift_identity/tests/test_step_up_sso_binding_2202.py astrolift_identity/tests/test_step_up_sso_peer_races_2202.py
```

The provider prompts for a fresh login, the callback returns the browser to
`<return-path>` with no `stepUp` refusal code, the elevated action proceeds
within `STEP_UP_AUTH_TTL_SECONDS`, and the test modules pass.

The identity rule follows [OpenID Connect Core, Claim Stability and Uniqueness](https://openid.net/specs/openid-connect-core-1_0.html#ClaimStabilityAndUniqueness).
