"""Tests for the public ``astroliftServerInfo`` handshake (#479).

The handshake is intentionally unauthenticated — clients call it
before login to decide which UI to render — and intentionally leaks
NO secrets / operator-private data. These tests pin that contract.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from unittest import mock

from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, TestCase
from django.test.utils import override_settings

from config.schema import schema
from core.capabilities_registry import SHIPPED_CAPABILITIES, shipped_capabilities
from core.schema.context import StrawberryContext

_QUERY = """
{
  astroliftServerInfo {
    version
    apiVersion
    installId
    installSlug
    installLabel
    region
    serverTime
    capabilities
    featureFlags { key enabled description }
    authMethods
  }
}
"""


def _anonymous_context() -> StrawberryContext:
    request = RequestFactory().get("/app/gql/config/")
    request.user = AnonymousUser()
    request.session = {}
    return StrawberryContext(request)


class AstroliftServerInfoTest(TestCase):
    def _execute(self) -> dict:
        result = schema.execute_sync(_QUERY, context_value=_anonymous_context())
        self.assertIsNone(result.errors, f"unexpected errors: {result.errors}")
        self.assertIsNotNone(result.data)
        info = result.data["astroliftServerInfo"]
        self.assertIsNotNone(info)
        return info

    # ---- Test 1: unauthenticated access works -----------------------

    def test_unauthenticated_caller_gets_full_payload(self) -> None:
        """A request with AnonymousUser must return the handshake.

        Multi-install mobile / CLI clients need this to probe the URL
        before the user logs in. If auth were required the install-
        picker UX would break.
        """
        info = self._execute()
        self.assertIn("version", info)
        self.assertIn("apiVersion", info)
        self.assertIn("installId", info)
        self.assertIn("installSlug", info)
        self.assertIn("serverTime", info)
        self.assertIn("capabilities", info)
        self.assertIn("featureFlags", info)
        self.assertIn("authMethods", info)

    def test_discovery_reports_explicit_workload_environment_review(self) -> None:
        self.assertIn("workloads.environment_targets", self._execute()["capabilities"])
        result = schema.execute_sync(
            '{ astroliftWorkloadActionTarget(workloadId: "11111111-1111-4111-8111-111111111111", '
            'environmentId: "22222222-2222-4222-8222-222222222222") { workloadId environmentId } }',
            context_value=_anonymous_context(),
        )
        self.assertIsNotNone(result.errors)
        self.assertIsNone(result.data["astroliftWorkloadActionTarget"])

    def test_native_metadata_capability_is_wired_but_does_not_authorize_a_model_read(self) -> None:
        self.assertIn("models.native_connection_metadata", self._execute()["capabilities"])
        projection = schema.execute_sync(
            '{ __type(name: "ClusterModelDeployment") { fields { name } } }',
            context_value=_anonymous_context(),
        )
        self.assertIsNone(projection.errors)
        self.assertIn("nativeConnection", {field["name"] for field in projection.data["__type"]["fields"]})
        read = schema.execute_sync(
            '{ clusterModelDeployment(organizationId: "11111111-1111-4111-8111-111111111111", '
            'id: "22222222-2222-4222-8222-222222222222") { nativeConnection { family configurationState } } }',
            context_value=_anonymous_context(),
        )
        self.assertIsNotNone(read.errors)
        self.assertIsNone(read.data["clusterModelDeployment"])

    def test_discovery_reports_wired_reviewed_cluster_installers(self) -> None:
        capabilities = self._execute()["capabilities"]
        self.assertIn("clusters.reviewed_agent_install", capabilities)
        self.assertIn("clusters.reviewed_log_collector_install", capabilities)
        result = schema.execute_sync(
            '{ __type(name: "Query") { fields { name } } ' "__schema { mutationType { fields { name } } } }",
            context_value=_anonymous_context(),
        )
        self.assertIsNone(result.errors)
        queries = {field["name"] for field in result.data["__type"]["fields"]}
        mutations = {field["name"] for field in result.data["__schema"]["mutationType"]["fields"]}
        self.assertIn("astroliftClusterAgentInstallReview", queries)
        self.assertIn("astroliftClusterLogCollectorReview", queries)
        self.assertIn("installClusterAgent", mutations)
        self.assertIn("astroliftInstallClusterLogCollector", mutations)

    # ---- Test 2: version matches settings.VERSION -------------------

    def test_version_matches_settings(self) -> None:
        from django.conf import settings as dj_settings

        info = self._execute()
        self.assertEqual(info["version"], dj_settings.VERSION)
        # apiVersion is the platform-version + schema fingerprint;
        # at minimum it must start with the platform version so old
        # clients can do a substring check.
        self.assertTrue(
            info["apiVersion"].startswith(dj_settings.VERSION),
            f"apiVersion {info['apiVersion']!r} must start with VERSION {dj_settings.VERSION!r}",
        )

    # ---- Test 3: capabilities list non-empty + every entry shipped -

    def test_capabilities_list_matches_registry(self) -> None:
        info = self._execute()
        caps = info["capabilities"]
        self.assertIsInstance(caps, list)
        self.assertGreater(len(caps), 0, "capabilities list must not be empty")
        # Every reported capability is in the curated registry — no
        # ad-hoc strings can sneak in from anywhere else.
        for key in caps:
            self.assertIn(
                key,
                SHIPPED_CAPABILITIES,
                f"unknown capability {key!r} leaked into handshake",
            )
        # The handshake itself advertises that it exists — clients on
        # old installs can detect handshake-absence by introspection.
        self.assertIn("server_info.handshake", caps)
        # Sorted for stable client-side diffing.
        self.assertEqual(caps, sorted(caps))
        self.assertEqual(set(caps), set(shipped_capabilities()))

    # ---- Test 4: no sensitive data leaks ---------------------------

    @override_settings(
        SECRET_KEY="this-is-a-secret-marker-do-not-leak-12345",
        AUTH0_CLIENT_SECRET="auth0-secret-marker-do-not-leak-67890",
    )
    def test_no_sensitive_data_in_response(self) -> None:
        """Capabilities and feature flags are public by design — but
        nothing else operator-private may slip into the response.
        """
        info = self._execute()
        # Coerce the full response (incl. nested feature flags) to a
        # single string and scan for secrets.
        blob = repr(info)
        self.assertNotIn("this-is-a-secret-marker-do-not-leak-12345", blob)
        self.assertNotIn("auth0-secret-marker-do-not-leak-67890", blob)
        # Generic patterns: nothing that looks like a private key or
        # an AWS key id should be in the payload.
        self.assertNotIn("BEGIN PRIVATE KEY", blob)
        self.assertNotIn("BEGIN RSA PRIVATE KEY", blob)
        self.assertFalse(
            re.search(r"AKIA[0-9A-Z]{16}", blob),
            "AWS access key id appears to be in the response",
        )

    # ---- Test 5: serverTime is ISO 8601 UTC ------------------------

    def test_server_time_is_iso8601_utc(self) -> None:
        info = self._execute()
        raw = info["serverTime"]
        self.assertIsInstance(raw, str)
        # Strawberry serializes datetime as ISO 8601. Parsing must
        # succeed and yield a timezone-aware value.
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        self.assertIsNotNone(parsed.tzinfo, f"serverTime {raw!r} must be timezone-aware")
        # Recent — within 60 seconds of now.
        now = datetime.now(UTC)
        delta = abs((now - parsed).total_seconds())
        self.assertLess(delta, 60, f"serverTime {raw!r} drifts {delta}s from wall clock")

    # ---- Test 6: featureFlags reflects Constance toggles -----------

    def test_feature_flags_reflect_constance(self) -> None:
        """Toggling a Constance value must change the next handshake.

        We patch the live Constance singleton so the test doesn't
        depend on the database row being writable in the test DB
        (constance uses its own model + cache that requires the
        database backend to be migrated).
        """
        from core.schema.types import server_info as si_module

        class _FakeConstance:
            TEMPORAL_ENABLED = True
            DEPLOY_PIPELINE_ENABLED = True
            EMAIL_NOTIFICATIONS = False
            ZENTINELLE_ENABLED = False
            ALLOW_SELF_APPROVE_DEPLOYS = False
            DEPLOY_TOKEN_LEGACY_PREFIX_ACCEPTED = True

        # First state: deploy pipeline ON.
        with mock.patch.dict(
            "sys.modules",
            {"constance": mock.MagicMock(config=_FakeConstance())},
        ):
            info = self._execute()
            flags = {f["key"]: f["enabled"] for f in info["featureFlags"]}
            self.assertTrue(flags["deploys.pipeline_enabled"])
            self.assertTrue(flags["workflows.temporal_enabled"])
            self.assertFalse(flags["notifications.email_enabled"])
            # Zentinelle governance surfaces default OFF (#1104).
            self.assertFalse(flags["zentinelle.enabled"])

        # Flip deploy pipeline OFF — handshake must reflect it.
        class _FakeConstanceOff(_FakeConstance):
            DEPLOY_PIPELINE_ENABLED = False
            EMAIL_NOTIFICATIONS = True
            ZENTINELLE_ENABLED = True

        with mock.patch.dict(
            "sys.modules",
            {"constance": mock.MagicMock(config=_FakeConstanceOff())},
        ):
            info = self._execute()
            flags = {f["key"]: f["enabled"] for f in info["featureFlags"]}
            self.assertFalse(flags["deploys.pipeline_enabled"])
            self.assertTrue(flags["notifications.email_enabled"])
            # Enabling the Constance flag surfaces the Zentinelle tabs.
            self.assertTrue(flags["zentinelle.enabled"])

        # Every entry in the public allow-list must be reported, in a
        # stable order across calls so client diffing is deterministic.
        info = self._execute()
        public_keys = [pub for (_c, pub, _d) in si_module._PUBLIC_FEATURE_FLAGS]
        reported_keys = [f["key"] for f in info["featureFlags"]]
        self.assertEqual(reported_keys, public_keys)

    # ---- Bonus pin: installId is stable across calls ---------------

    def test_install_id_is_stable_across_calls(self) -> None:
        info_a = self._execute()
        info_b = self._execute()
        self.assertEqual(info_a["installId"], info_b["installId"])
        self.assertTrue(info_a["installId"])

    # ---- Bonus pin: authMethods always lists session_cookie --------

    def test_auth_methods_always_lists_session_cookie(self) -> None:
        info = self._execute()
        self.assertIn("session_cookie", info["authMethods"])
