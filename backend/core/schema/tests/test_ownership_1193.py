"""Ownership / tenancy checks for the core generic global-ID mutation
framework (#1193): notification upsert, image uploads, and the form
submission subscription.

These resolvers predate Astrolift's org model and lacked per-row owner
checks. Each test asserts the OWNER is allowed and a NON-owner (other
user / other org) is denied — mirroring the safe sibling in the same
module (``notification_read`` / ``astrolift_deployment_lifecycle_stream``).

``notification`` upsert is the exception (#1949): a ``guid`` in the input
names an existing row for an update, and ``NotificationSerializer`` is a
plain ModelSerializer with ``user``/``subject``/``message`` all writable,
so an ownership check alone would still let the OWNER re-address or
rewrite a notification they only own as its recipient. Every ``guid``
input is refused outright now, owner included, so those two cases are
covered together below rather than as an owner/non-owner pair.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.contrib.auth import get_user_model
from graphql import GraphQLError

from core.models import Profile
from core.models.notification import Notification
from core.models.upload import Upload
from core.schema.mutations.notification import NotificationMutations
from core.schema.mutations.upload import UploadMutations
from core.schema.subscriptions import _CoreSubscription
from core.tenancy import TenantContext, tenant_context

User = get_user_model()


def _info(user):
    """Minimal ``info``-shaped object accepted by the resolver bodies."""
    return SimpleNamespace(context=SimpleNamespace(user=user, request=SimpleNamespace(user=user)))


# ---------------------------------------------------------------------------
# notification upsert (#1949: any `guid` update is refused, owner included)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_notification_upsert_with_guid_is_refused_even_for_the_owner():
    user_a = User.objects.create_user(username="n1193-o", email="n1193o@t.local")
    notif_a = Notification.objects.create(user=user_a, subject="mine", message="m", created_by=user_a)

    result = NotificationMutations().notification(
        info=_info(user_a), input={"guid": str(notif_a.pk), "subject": "hijacked"}
    )

    assert result.ok is False
    assert result.errors[0].field == "guid"
    notif_a.refresh_from_db()
    assert notif_a.subject == "mine"


@pytest.mark.django_db
def test_notification_upsert_with_guid_is_refused_for_another_users_notification():
    user_a = User.objects.create_user(username="n1193-a", email="n1193a@t.local")
    user_b = User.objects.create_user(username="n1193-b", email="n1193b@t.local")
    notif_b = Notification.objects.create(
        user=user_b, subject="B-private", message="secret", created_by=user_b
    )

    result = NotificationMutations().notification(
        info=_info(user_a), input={"guid": str(notif_b.pk), "subject": "hijacked"}
    )

    assert result.ok is False
    notif_b.refresh_from_db()
    assert notif_b.subject == "B-private"


@pytest.mark.django_db
def test_notification_create_without_guid_still_works():
    """Creates (no `guid` in the input) are unaffected by the #1949 fix."""
    user_a = User.objects.create_user(username="n1193-c", email="n1193c@t.local")

    result = NotificationMutations().notification(
        info=_info(user_a),
        input={"user": str(user_a.pk), "subject": "hello", "message": "world"},
    )

    assert result.ok is True, result.errors
    assert Notification.objects.filter(user=user_a, subject="hello", message="world").exists()


# ---------------------------------------------------------------------------
# profile_image_field_upload — self-ownership check
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_profile_image_upload_denies_other_users_profile():
    user_a = User.objects.create_user(username="p1193-a", email="p1193a@t.local")
    user_b = User.objects.create_user(username="p1193-b", email="p1193b@t.local")
    with pytest.raises(GraphQLError, match="Profile does not belong to user"):
        UploadMutations().profile_image_field_upload(
            info=_info(user_a),
            global_id=user_b.profile.global_id,
            mimetype="image/png",
            field=Profile.ImageField.AVATAR,
        )
    user_b.profile.refresh_from_db()
    assert user_b.profile.avatar_id is None


@pytest.mark.django_db
def test_profile_image_upload_owner_allowed():
    user_a = User.objects.create_user(username="p1193-o", email="p1193o@t.local")
    # Force the whitelisted (non-approval) path and stub the actual save so the
    # test isolates the ownership gate from the draft/approval + S3 machinery.
    with (
        patch.object(Profile, "whitelist_fields", return_value=["avatar"]),
        patch.object(UploadMutations, "_save_profile_upload", return_value=None) as mock_save,
    ):
        result = UploadMutations().profile_image_field_upload(
            info=_info(user_a),
            global_id=user_a.profile.global_id,
            mimetype="image/png",
            field=Profile.ImageField.AVATAR,
        )
    assert result.upload is None
    mock_save.assert_called_once()


# ---------------------------------------------------------------------------
# pre_signed_url_image_upload — target-ownership check
# ---------------------------------------------------------------------------


def _org_user(slug):
    from organization.models import Organization, OrganizationMember

    org = Organization.objects.create(name=f"UpOrg-{slug}")
    user = User.objects.create_user(username=f"up-{slug}", email=f"up-{slug}@t.local")
    OrganizationMember.objects.create(organization=org, member=user, is_active=True)
    user.profile.active_organization = org
    user.profile.save()
    return org, user


@pytest.mark.django_db
def test_presigned_upload_denies_target_in_other_org():
    org_a, user_a = _org_user("xa")
    _org_b, user_b = _org_user("xb")
    target = Upload.objects.create(
        organization=org_a,
        created_by=user_a,
        content_type="image/png",
        public_url="https://example.invalid/a",
        pre_signed_url="x",
    )
    with patch("core.schema.mutations.upload.create_upload") as mock_create:
        with pytest.raises(GraphQLError, match="Not authorized"):
            UploadMutations().pre_signed_url_image_upload(
                info=_info(user_b),
                global_id=target.global_id,
                mimetype="image/png",
            )
    # Denied before any upload work / presigned URL was issued.
    assert mock_create.call_count == 0


@pytest.mark.django_db
def test_presigned_upload_allows_owned_target():
    org_a, user_a = _org_user("oa")
    target = Upload.objects.create(
        organization=org_a,
        created_by=user_a,
        content_type="image/png",
        public_url="https://example.invalid/o",
        pre_signed_url="x",
    )
    fake_upload = SimpleNamespace(
        public_url="https://fake/pub", pre_signed_url="https://fake/put", id=uuid4()
    )
    with patch(
        "core.schema.mutations.upload.create_upload", return_value=fake_upload
    ) as mock_create:
        result = UploadMutations().pre_signed_url_image_upload(
            info=_info(user_a),
            global_id=target.global_id,
            mimetype="image/png",
        )
    assert mock_create.call_count == 1
    assert result.pre_signed_url == "https://fake/put"
    assert result.public_url == "https://fake/pub"


# ---------------------------------------------------------------------------
# form_submission_received subscription — org scoping
# ---------------------------------------------------------------------------


async def _drain(agen, limit=3):
    out = []
    async for item in agen:
        out.append(item)
        if len(out) >= limit:
            break
    return out


def test_form_submission_subscription_no_org_yields_nothing():
    """No resolved tenant org → the stream fails closed and yields nothing."""
    info = SimpleNamespace(context=SimpleNamespace())
    out = asyncio.run(_drain(_CoreSubscription().form_submission_received(info=info, slug="any")))
    assert out == []


@pytest.mark.django_db(transaction=True)
def test_form_submission_subscription_scopes_to_caller_org():
    """An org-A caller must not receive another tenant's (org-B) submissions.

    The org-B submission is injected AFTER the stream's initial seed but
    BEFORE its first loop fetch — an unscoped stream would yield it there;
    the org-A-scoped stream must not.
    """
    from asgiref.sync import sync_to_async
    from astrolift_forms.models import FormDefinition, FormSubmission
    from astrolift_identity.models import Organization as IdentityOrg

    org_a = IdentityOrg.objects.create(name="SubOrgA", slug="sub-org-a-1193")
    org_b = IdentityOrg.objects.create(name="SubOrgB", slug="sub-org-b-1193")
    form_b = FormDefinition.objects.create(organization=org_b, name="Shared", slug="shared-form-1193")

    class _Stop(Exception):
        pass

    state = {"n": 0}

    async def fake_sleep(_secs):
        state["n"] += 1
        if state["n"] == 1:
            await sync_to_async(FormSubmission.objects.create)(
                form=form_b, organization=org_b, form_version=1, payload={}
            )
            return
        raise _Stop()

    out = []

    async def run():
        with tenant_context(TenantContext(organization_id=org_a.id)):
            agen = _CoreSubscription().form_submission_received(
                info=SimpleNamespace(context=SimpleNamespace()),
                slug="shared-form-1193",
            )
            try:
                async for item in agen:
                    out.append(item)
            except _Stop:
                pass

    with patch("core.schema.subscriptions.asyncio.sleep", fake_sleep):
        asyncio.run(run())

    assert out == []
    # Sanity: the org-B submission really exists — the empty stream above is
    # org scoping, not an empty table.
    assert FormSubmission.objects.filter(organization=org_b).count() == 1
