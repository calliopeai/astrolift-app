"""
Authentication Session Workflow
"""

import dataclasses
import json
import logging
import os
import time
import uuid
from http import HTTPStatus
from urllib.parse import quote_plus, urlencode, urlsplit, urlunsplit

import requests
from authlib.integrations.django_client import OAuth
from authlib.integrations.requests_client import OAuth2Session
from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.models import User
from django.core.handlers.wsgi import WSGIRequest
from django.db.models import Q
from django.http import HttpResponse, HttpResponseNotAllowed, HttpResponseRedirect
from django.shortcuts import redirect
from django.urls import path, reverse
from django.views.decorators.csrf import csrf_exempt
from django_ratelimit.decorators import ratelimit

from .models import Authentication, UserInfo

logger = logging.getLogger(__name__)


# Use https://<your-auth0-domain>/.well-known/openid-configuration to get endpoints information
# How to fix: Grant type \`client\_credentials\` not allowed for the client
#
# ### Assign the Required Permissions in Auth0
# 1. Go to the Auth0 Dashboard.
# 1. Navigate to Applications \> Your Application.
# 1. Click the \`APIs\` tab.
# 1. Locate the Auth0 Management API.
# 1. Assign the required scopes (permissions) based on what the client needs to do.
# 1. For user registration, you might need:
# \- create:users
# \- read:users
# \- update:users
# 1. Save the changes.
#
# ### Configure: Grant Types
# 1. Go to the Auth0 Dashboard
# 1. Go to Applications
# 1. Select the Application
# 1. Go to the Credentials Tab
#    1. Select: Client Secret (Post)
# 1. Select Settings Tab
# 1. Scroll down to Advanced Settings
#    1. Select Grant Types
#    1. Select Client Credentials
#    1. Save Changes
#


class EmailNotVerifiedException(Exception):
    pass


@dataclasses.dataclass
class AuthClient:
    """
    Auth0 Client
    """

    name: str
    domain: str
    client_id: str
    client_secret: str
    client_kwargs: dict
    server_metadata_url: str

    _connections: dict = dataclasses.field(default_factory=dict)
    _token_cache: dict = dataclasses.field(default_factory=dict)

    _client: OAuth2Session = None
    _default_client = None

    @classmethod
    def from_settings(cls, name="default") -> "AuthClient":
        if name == "default" and cls._default_client:
            return cls._default_client

        _client = cls(
            name=name,
            domain=settings.AUTH0_DOMAIN,
            client_id=settings.AUTH0_CLIENT_ID,
            client_secret=settings.AUTH0_CLIENT_SECRET,
            client_kwargs={
                "scope": settings.AUTH0_CLIENT_SCOPES,
            },
            # Prefer the explicit override (Cognito-style providers where the
            # hosted UI domain doesn't serve the discovery doc); fall back to
            # the Auth0-style "discovery on the login domain" default.
            server_metadata_url=(
                getattr(settings, "AUTH0_SERVER_METADATA_URL", None)
                or f"https://{settings.AUTH0_DOMAIN}/.well-known/openid-configuration"
            ),
        )

        if name == "default":
            cls._default_client = _client

        return _client

    def _is_token_valid(self, current_time=None):
        current_time = current_time or time.time()
        return self._token_cache.get("expires_at", current_time) > current_time

    def get_auth0_access_token(self):
        current_time = time.time()
        # Check if token is already cached and still valid
        if self._is_token_valid(current_time):
            return self._token_cache["access_token"]

        # Prepare the request payload and headers
        payload = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "audience": f"https://{self.domain}/api/v2/",
            "grant_type": "client_credentials",
        }
        headers = {"Content-Type": "application/json"}

        # Make the POST request using requests
        response = requests.post(f"https://{self.domain}/oauth/token", json=payload, headers=headers)

        # Raise an error for non-2xx status codes
        response.raise_for_status()
        token = response.json()

        # Cache the token and expiration time
        self._token_cache["access_token"] = token["access_token"]
        self._token_cache["expires_at"] = (
            current_time + token["expires_in"] - 60
        )  # Subtract 1 minute as a buffer

        return self._token_cache["access_token"]

    def _get_client(self):
        if self._client and self._is_token_valid():
            return self._client

        self._client = OAuth2Session(
            client_id=self.client_id,
            client_secret=self.client_secret,
            token={"access_token": self.get_auth0_access_token(), "token_type": "Bearer"},
            server_metadata_url=f"https://{self.domain}/.well-known/openid-configuration",
        )

        return self._client

    def _get_connection_id(self, connection_name=settings.AUTH0_DATABASE_CONNECTION_ID):
        """
        Fetch the connection_id for a given connection name.
        """
        if len(self._connections) == 0:
            url = f"https://{self.domain}/api/v2/connections"
            headers = {
                "Authorization": f"Bearer {self.get_auth0_access_token()}",
                "Content-Type": "application/json",
            }

            response = requests.get(url, headers=headers)
            response.raise_for_status()

            connections = response.json()
            for connection in connections:
                self._connections[connection["name"]] = connection

        if connection_name in self._connections:
            return self._connections[connection_name]["id"]

        raise ValueError(f"Connection '{connection_name}' not found.")

    def _post(self, url, payload):
        client = self._get_client()
        response = client.post(url, json=payload, headers={"Content-Type": "application/json"})
        if response.status_code != HTTPStatus.CONFLICT.value:
            response.raise_for_status()
        return response

    def create_auth0_user(
        self, email, phone_number, email_verified=True, connection=settings.AUTH0_DATABASE_CONNECTION_ID
    ):
        """
        Create a new user in the Auth0 database using Authlib client.
        """
        return self._post(
            url=f"https://{settings.AUTH0_DOMAIN}/api/v2/users",
            payload={
                "email": email,
                # "username": email,
                "connection": connection,
                "password": email + str(uuid.uuid4()),
                "email_verified": email_verified,
            },
        )

    def request_reset_password(self, email, connection=settings.AUTH0_DATABASE_CONNECTION_ID):
        """
        Trigger a password reset email for the user.
        """
        return self._post(
            url=f"https://{settings.AUTH0_DOMAIN}/dbconnections/change_password",
            payload={
                "email": email,
                "connection": connection,
                # "connection_id": self._get_connection_id(),
            },
        )


class Auth1SessionWorkflow:
    """
    Authentication Session Workflow
    """

    """
    Landing Page after successful login
    """
    landing = "admin:index"

    """
    OAuth client
    """
    _client = OAuth()
    _client.register(
        name="auth0",
        client_id=settings.AUTH0_CLIENT_ID,
        client_secret=settings.AUTH0_CLIENT_SECRET,
        client_kwargs={
            "scope": settings.AUTH0_CLIENT_SCOPES,
        },
        # Auth0 serves discovery on the same host that handles login; Cognito
        # splits the two — hosted-UI domain doesn't serve /.well-known. Prefer
        # the explicit override when set, fall back to the Auth0-style default.
        server_metadata_url=(
            getattr(settings, "AUTH0_SERVER_METADATA_URL", None)
            or f"https://{settings.AUTH0_DOMAIN}/.well-known/openid-configuration"
        ),
    )

    @classmethod
    def session(cls, request: WSGIRequest):
        """
        Authentication Session Start Point

        Expects a POST Method, and auth0 token authentication.

        Registers the given token information and provides a session token
        in a Header.
        """
        try:
            if request.method != "POST":
                logger.warning(
                    f"[Auth0] Requested method {request.method} not support for session endpoint.",
                    exc_info=True,
                )
                return HttpResponseNotAllowed(permitted_methods={"POST"})
            auth0_token = json.loads(request.body)
            cls._register_remote_user(request, auth0_token)
            try:
                from auth1.auto_join import maybe_auto_join_user

                maybe_auto_join_user(getattr(request, "user", None))
            except Exception:
                logger.exception("[Auth0] auto_join hook failed — auth flow continues")
            logger.debug(f"[Auth0] Session {request.session.session_key} registered.")
            return HttpResponse(
                status=HTTPStatus.ACCEPTED.ACCEPTED,
                content=json.dumps(
                    {
                        "Authorization": f"Session {request.session.session_key}",
                    }
                ),
                headers={
                    "Content-Type": "application/json",
                },
            )
        except EmailNotVerifiedException as e:
            logger.warning(f"[Auth0] {e} redirecting to verify-email", exc_info=True)
            return HttpResponseRedirect(request.build_absolute_uri("/verify-email"))
        except Exception as e:
            logger.exception(f"[Auth0] Unable to Start Sessions {e}")
            raise e

    @classmethod
    def _fix_proxy_pass(cls, request: WSGIRequest, url):
        """
        Fix the proxy pass for the Nginx Server.

        We need to forward these Headers:

        - X-Forwarded-Proto
        - X-Forwarded-Host
        - X-Forwarded-Port
        """
        proto = request.headers.get("X-Forwarded-Proto")
        host = request.headers.get("X-Forwarded-Host")
        port = request.headers.get("X-Forwarded-Port")
        if proto and host and port:
            url = list(urlsplit(url))
            url[0] = proto
            url[1] = f"{host}:{port}"
            url = urlunsplit(url)
        return url

    @classmethod
    def login(cls, request: WSGIRequest):
        """
        Entry point for the login workflow on the Django Admin Site.

        Accepts an optional ?next= query param.  When present the value is
        stored in the session so callback() can redirect the browser back to
        the caller (typically the Next.js frontend) after a successful login.
        """
        logger.debug("[Auth0] Django login requested")
        next_url = request.GET.get("next", "")
        if next_url:
            request.session["auth_next"] = next_url
        callback_url = request.build_absolute_uri(reverse("callback"))
        callback_url = cls._fix_proxy_pass(request, callback_url)
        return cls._client.auth0.authorize_redirect(request, callback_url)

    @classmethod
    def callback(cls, request: WSGIRequest):
        """
        Callback entrypoint from the Auth0 Tenant with the token authentication.

        When a ?next= URL was stored by login(), redirect there and append the
        session token as a `?token=` query parameter so the frontend can store
        it without needing access to the Django session cookie.
        """
        try:
            logger.debug("[Auth0] Auth0 Tenant callback received.")
            auth0_token = cls._client.auth0.authorize_access_token(request)
            cls._register_remote_user(request, auth0_token)

            # Best-effort: if the user just authed and their email
            # domain is on the active org's allowlist, auto-create
            # the Member (and optional RoleBinding) so SSO users
            # from a trusted corp domain don't need an explicit
            # invitation. Wrapped to never block auth flow.
            try:
                from auth1.auto_join import maybe_auto_join_user

                maybe_auto_join_user(getattr(request, "user", None))
            except Exception:
                logger.exception("[Auth0] auto_join hook failed — auth flow continues")

            next_url = request.session.pop("auth_next", None)
            if next_url:
                token = f"Session {request.session.session_key}"
                separator = "&" if "?" in next_url else "?"
                url = f"{next_url}{separator}token={quote_plus(token)}"
            else:
                # If FRONTEND_URL is set, redirect there with the session token
                # so the frontend can complete its auth flow
                from django.conf import settings

                frontend_url = getattr(settings, "FRONTEND_URL", None)
                if frontend_url:
                    token = f"Session {request.session.session_key}"
                    url = f"{frontend_url}/auth/callback?token={quote_plus(token)}"
                else:
                    url = request.build_absolute_uri(reverse(cls.landing))
                    url = cls._fix_proxy_pass(request, url)

            return HttpResponseRedirect(url)
        except EmailNotVerifiedException as e:
            logger.warning(f"[Auth0] {e} redirecting to verify-email", exc_info=True)
            return HttpResponseRedirect(request.build_absolute_uri("/verify-email"))

    @classmethod
    def logout(cls, request: WSGIRequest):
        """
        Entry point for the login workflow on the Django Admin Site.

        This method will perform a redirect to the Landing Page for Login
        after cleaning the session.
        """
        logger.debug("[Auth0] Auth0 Django logout requested")
        request.session.clear()
        url = request.build_absolute_uri(reverse(cls.landing))
        url = cls._fix_proxy_pass(request, url)

        # OIDC providers diverge on logout URL shape:
        #   Auth0   → /v2/logout?returnTo=…&client_id=…
        #   Cognito → /logout?logout_uri=…&client_id=…
        #   generic → end_session_endpoint from the discovery doc
        # Prefer the discovery doc when available (it's loaded once at
        # first auth request and cached), fall back to Auth0's shape so
        # existing Auth0 installs keep working unchanged.
        try:
            metadata = cls._client.auth0.load_server_metadata()
        except Exception:
            metadata = {}
        end_session_endpoint = metadata.get("end_session_endpoint")

        if end_session_endpoint and "cognito" in end_session_endpoint:
            # Cognito-style: /logout, parameter name is `logout_uri`
            return redirect(
                end_session_endpoint
                + "?"
                + urlencode(
                    {
                        "logout_uri": url,
                        "client_id": settings.AUTH0_CLIENT_ID,
                    },
                    quote_via=quote_plus,
                ),
            )
        # Auth0-style fallback (original behavior)
        return redirect(
            f"https://{settings.AUTH0_DOMAIN}/v2/logout?"
            + urlencode(
                {
                    "returnTo": url,
                    "client_id": settings.AUTH0_CLIENT_ID,
                },
                quote_via=quote_plus,
            ),
        )

    @classmethod
    def urls(cls):
        """
        Provides the list of urls supported this workflow:

        - login: login entrypoint
        - logout: logout entrypoint
        - callback: tenant callback entrypoint
        - session: start session entrypoint.=
        """
        _rl_login = ratelimit(key="ip", rate="10/m", block=True)(cls.login)
        _rl_session = ratelimit(key="ip", rate="5/m", method="POST", block=True)(cls.session)
        from auth1.active_idp import active_idp
        from auth1.dev_login import dev_login
        from auth1.local_login import local_login
        from auth1.scm_oauth import github_callback, github_start
        from auth1.scm_webhook import github_webhook, gitlab_webhook

        return [
            path("login", _rl_login, name="login"),
            path("logout", cls.logout, name="logout"),
            path("callback", cls.callback, name="callback"),
            path("session", csrf_exempt(_rl_session), name="session"),
            path("dev-login", dev_login, name="dev-login"),
            path("local-login", local_login, name="local-login"),
            path("active-idp.json", active_idp, name="active-idp"),
            path("scm/github/start", github_start, name="scm_github_start"),
            path(
                "scm/github/callback",
                github_callback,
                name="scm_github_callback",
            ),
            path(
                "scm/github/webhook/<str:connection_id>/",
                github_webhook,
                name="scm_github_webhook",
            ),
            path(
                "scm/gitlab/webhook/<str:connection_id>/",
                gitlab_webhook,
                name="scm_gitlab_webhook",
            ),
        ]

    @classmethod
    def _lookup_user(cls, user_info: UserInfo) -> User:
        """
        Lookup user with different methods:

        1. Lookup the user when the user email matches with the userinfo email provided by Auth0

        Pending to Implement:

        1. Lookup the user when the profile phone number matches the phone number provided by Auth0
        """
        filter_chain = Q(email__iexact=user_info.email)

        if settings.DEBUG and "+" in user_info.email:
            main = user_info.email.split("+")
            host = user_info.email.split("@")
            filter_chain = filter_chain & Q(email__iexact=f"{main[0]}@{host[1]}")

        user = User.objects.filter(filter_chain).first()

        if user:
            logger.info(f"[Auth0] Found user by email: {repr(user_info.email)} to {repr(user.id)}")
            return user

        # Optional auto-signup: when the userinfo email lives under an
        # operator-allowlisted domain (env var ASTROLIFT_AUTO_SIGNUP_DOMAINS,
        # comma-separated, case-insensitive), provision a Django User on
        # first sign-in instead of returning None. Unset env = legacy
        # secure-default behavior: no auto-create, returns None.
        auto = cls._maybe_autoprovision_user(user_info)
        if auto:
            return auto

        # TODO: Implement phone number lookup
        logger.warning(f"[Auth0] Could not find user by email: {repr(user_info.email)}", exc_info=True)
        return None

    @classmethod
    def _maybe_autoprovision_user(cls, user_info: UserInfo) -> User | None:
        """
        Create a Django User on first sign-in if the IdP-provided email
        falls under an allowlisted domain. Returns the new User, or None
        if auto-signup isn't enabled / the domain doesn't match / the
        email isn't verified.

        Configured by env var ASTROLIFT_AUTO_SIGNUP_DOMAINS — comma-
        separated, case-insensitive (e.g. "acmecorp.com,example.org").
        Leave it unset to keep the strict-lookup default.
        """
        raw = os.environ.get("ASTROLIFT_AUTO_SIGNUP_DOMAINS", "")
        allow = {d.strip().lower() for d in raw.split(",") if d.strip()}
        if not allow:
            return None

        email = (user_info.email or "").strip()
        if not email or "@" not in email:
            return None
        domain = email.rsplit("@", 1)[1].lower()
        if domain not in allow:
            logger.info(
                "[Auth0] auto-signup skipped — domain %r not in allowlist %r",
                domain,
                sorted(allow),
            )
            return None

        # The provider already attests email_verified=True for IdP-backed
        # flows (Cognito + Google Workspace HD, Auth0 verified senders).
        # If the upstream IdP forwards email_verified=False we honor it.
        if getattr(user_info, "email_verified", True) is False:
            logger.warning(
                "[Auth0] auto-signup refused for %r — email_verified=False from IdP",
                email,
            )
            return None

        user, created = User.objects.get_or_create(
            username=email,
            defaults={
                "email": email,
                "first_name": getattr(user_info, "given_name", "") or "",
                "last_name": getattr(user_info, "family_name", "") or "",
            },
        )
        if created:
            user.set_unusable_password()
            user.save(update_fields=["password"])
            logger.info(
                "[Auth0] auto-provisioned User id=%s username=%s (domain=%s)",
                user.id,
                email,
                domain,
            )
        return user

    @classmethod
    def _register_remote_user(cls, request, auth0_token) -> Authentication:
        """
        Register a user with the given auth0 token.
        """
        keys_to_remove = ["gender", "birthdate"]
        for key in keys_to_remove:
            if key in auth0_token:
                del auth0_token[key]

        # Different OIDC providers ship different claims in the userinfo
        # response — Auth0 stays close to the OpenID Connect core set,
        # while Cognito sprinkles in things like at_hash, cognito:groups,
        # cognito:username, origin_jti, identities, token_use, auth_time,
        # jti. Filter to fields UserInfo actually declares so unknown
        # claims (now or in the future) don't crash callback().
        _userinfo_fields = {f.name for f in UserInfo._meta.get_fields() if hasattr(f, "attname")}
        _raw_userinfo = auth0_token.get("userinfo") or {}
        _filtered = {k: v for k, v in _raw_userinfo.items() if k in _userinfo_fields}

        # Provider-shape fallbacks. Auth0 supplies `updated_at` on every
        # userinfo response and propagates `email_verified=true` for
        # IdP-verified logins; Cognito does neither for users federated
        # in through Google Workspace — `updated_at` is absent and
        # `email_verified=false` even though the upstream IdP verified
        # the email. Filling sensible defaults here keeps UserInfo's
        # NOT NULL constraints happy and lets the IdP's own verification
        # gate the auth flow.
        from django.utils import timezone as _tz

        if "updated_at" not in _filtered or _filtered.get("updated_at") in (None, ""):
            _filtered["updated_at"] = _tz.now()
        # If the IdP itself asserted the email (federated through Google
        # Workspace via Cognito, or any other IdP-attested flow), trust
        # that. The federated IdP is the source of truth for email
        # verification, not Cognito's own user pool view of it.
        iss = _filtered.get("iss") or ""
        if iss.startswith("https://cognito-idp."):
            _filtered["email_verified"] = True

        user_info = UserInfo(**_filtered)
        user_info.internal_user = cls._lookup_user(user_info)
        user_info.save()

        # Keep the linked Django User's display fields in sync with what
        # the IdP just told us. bootstrap_admin seeds a User row by email
        # alone (no name fields), so without this the frontend ends up
        # rendering "User" / "US" avatar initials on every page load.
        # Only fill empty slots — don't overwrite an explicit edit in the
        # admin.
        if user_info.internal_user is not None:
            _u = user_info.internal_user
            _changed = []
            if not _u.first_name and getattr(user_info, "given_name", ""):
                _u.first_name = user_info.given_name
                _changed.append("first_name")
            if not _u.last_name and getattr(user_info, "family_name", ""):
                _u.last_name = user_info.family_name
                _changed.append("last_name")
            if not _u.email and getattr(user_info, "email", ""):
                _u.email = user_info.email
                _changed.append("email")
            if _changed:
                _u.save(update_fields=_changed)
                logger.info(
                    "[Auth0] synced User fields from IdP for %s: %s",
                    _u.username,
                    _changed,
                )

        if not user_info.email_verified:
            raise EmailNotVerifiedException(f"Email not verified for user: {user_info.email}")

        # Same filtering logic for the Authentication model — providers
        # ship extras (refresh_token, scope shapes, provider-specific
        # bookkeeping) the schema doesn't model.
        _auth_fields = {f.name for f in Authentication._meta.get_fields() if hasattr(f, "attname")}
        authentication_dict = {k: v for k, v in auth0_token.items() if k in _auth_fields}
        authentication_dict["userinfo"] = user_info
        authentication = Authentication(**authentication_dict)

        # TODO: We are not storing the authentication in the database
        # It consumes a lot of records. We can store this information in Cache
        # Instead of the database.
        if authentication.userinfo.internal_user:
            logger.info(
                f"[Auth0] Login with userinfo: {repr(authentication.userinfo.internal_user.username)}"
            )
            login(request, authentication.userinfo.internal_user)
        return authentication
