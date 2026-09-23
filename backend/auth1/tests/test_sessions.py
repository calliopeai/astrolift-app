from unittest.mock import MagicMock

from django.contrib.auth.models import User

from auth1.sessions import Auth1SessionWorkflow
from core.tests.utils.base_test import BaseTest


class SessionsTest(BaseTest):
    def setUp(self):
        super().setUp()
        User.objects.get_or_create(
            username="sessions_test_user",
            defaults={"email": "testuser@astrolift.dev"},
        )

    def _make_user_info(self, email):
        user_info = MagicMock()
        user_info.email = email
        return user_info

    def test_lookup_user_find_user_by_email(self):
        email = "testuser@astrolift.dev"
        user = Auth1SessionWorkflow._lookup_user(self._make_user_info(email))
        self.assertEqual(user.email, email)

    def test_lookup_user_find_user_by_email_with_caps(self):
        email = "TestUser@astrolift.dev"
        user = Auth1SessionWorkflow._lookup_user(self._make_user_info(email))
        self.assertEqual(user.email, email.lower())

    def test_lookup_user_dont_find_unknown_user(self):
        user = Auth1SessionWorkflow._lookup_user(self._make_user_info("unknown@astrolift.dev"))
        self.assertIsNone(user)

    def test_device_approval_return_uses_session_cookie_without_url_token(self):
        approval = "/app/cli/auth/device/pending-session/"
        self.assertEqual(Auth1SessionWorkflow._post_auth_next_url(approval, "secret-session"), approval)

    def test_frontend_return_still_receives_session_token(self):
        callback = "https://app.example/auth/callback"
        self.assertEqual(
            Auth1SessionWorkflow._post_auth_next_url(callback, "secret-session"),
            "https://app.example/auth/callback?token=Session+secret-session",
        )
