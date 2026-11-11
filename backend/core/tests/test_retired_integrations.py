"""Retired chat endpoints stay absent while profile updates remain local."""

import pytest
import requests
from django.contrib.auth import get_user_model
from django.db import connection

from config.schema import schema
from core.models import Profile

pytestmark = pytest.mark.django_db


def test_profile_name_update_does_not_call_external_integrations(monkeypatch):
    monkeypatch.setattr(
        "core.documents.ProfileDocument.index_profile", classmethod(lambda cls, profile: None)
    )
    monkeypatch.setattr("core.documents.ProfileDocument.delete_profile", classmethod(lambda cls, guid: None))

    def refuse_network(*args, **kwargs):
        pytest.fail("Profile name updates must not call an external integration")

    monkeypatch.setattr(requests.Session, "request", refuse_network)
    user = get_user_model().objects.create_user(
        username="retired-hooks", first_name="Before", last_name="Name"
    )
    profile_id = user.profile.pk
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE core_profile SET rocket_id = %s WHERE gid = %s", ["legacy-profile-id", profile_id]
        )
        cursor.execute(
            "UPDATE core_historicalprofile SET rocket_id = %s WHERE gid = %s",
            ["legacy-history-id", profile_id],
        )
    _, draft = Profile.objects.get_draft(user.profile)
    draft.first_name = "After"
    draft.last_name = "Updated"
    draft.save()

    Profile.objects.promote_draft(draft)

    user.refresh_from_db()
    assert (user.first_name, user.last_name) == ("After", "Updated")
    with connection.cursor() as cursor:
        cursor.execute("SELECT rocket_id FROM core_profile WHERE gid = %s", [profile_id])
        assert cursor.fetchone() == ("legacy-profile-id",)
        cursor.execute(
            "SELECT COUNT(*) FROM core_historicalprofile WHERE gid = %s AND rocket_id = %s",
            [profile_id, "legacy-history-id"],
        )
        assert cursor.fetchone()[0] > 0


def test_retired_chat_token_mutation_is_not_exposed():
    result = schema.execute_sync("mutation { generateRocketChatToken }")
    assert result.errors
    assert any("Cannot query field 'generateRocketChatToken'" in error.message for error in result.errors)
