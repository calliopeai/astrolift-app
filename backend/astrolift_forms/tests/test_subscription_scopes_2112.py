"""Subscription admission and cancellation use real form rows and grants."""

import asyncio
from types import SimpleNamespace

import pytest
from asgiref.sync import sync_to_async

from astrolift_forms.models import FormDefinition, FormSubmission
from astrolift_forms.tests.test_org_scopes_2112 import (
    _form_world,  # noqa: F401
    _grant,
    _make_token,
    _tenant_value,
)
from astrolift_identity.api_tokens import reset_current_api_token, set_current_api_token
from core.schema.subscriptions import _CoreSubscription
from core.tenancy import tenant_context

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.asyncio]


def _info(world, *, token=None, actor=True):
    return SimpleNamespace(
        context=SimpleNamespace(
            user=world.user,
            _ws_tenant=_tenant_value(world, "sibling", actor=actor),
            _ws_api_token=token,
        )
    )


async def _drain(world, info, slug=None):
    handle = set_current_api_token(None)
    try:
        with tenant_context(None):
            stream = _CoreSubscription().form_submission_received(info, slug=slug or world.form.slug)
            try:
                return [event async for event in stream]
            finally:
                await stream.aclose()
    finally:
        reset_current_api_token(handle)


@pytest.mark.parametrize(
    "authority",
    [
        "TEAM",
        "PROJECT",
        "foreign-role",
        "no-actor",
        "team-token",
        "operator-team-token",
        "foreign-org-token",
        "read-token",
    ],
)
async def test_subscription_refusal_is_silent_before_polling(world, monkeypatch, authority):
    def setup():
        _grant(
            world,
            authority if authority in {"TEAM", "PROJECT"} else "ORG",
            foreign=authority == "foreign-role",
        )
        if authority == "operator-team-token":
            world.user.is_superuser = True
            world.user.save(update_fields=["is_superuser"])
        ceilings = {
            "team-token": "team",
            "operator-team-token": "operator-team",
            "foreign-org-token": "foreign-org",
            "read-token": "read-apps",
        }
        return _make_token(world, ceiling=ceilings[authority]) if authority in ceilings else None

    token = await sync_to_async(setup)()

    async def must_not_poll(_seconds):
        pytest.fail("a refused subscription started polling")

    monkeypatch.setattr("core.schema.subscriptions.asyncio.sleep", must_not_poll)
    assert await _drain(world, _info(world, token=token, actor=authority != "no-actor")) == []


@pytest.mark.parametrize("bearer", [False, True])
async def test_subscription_pins_identity_before_deferred_admission_and_emits_only_own_form(
    world, monkeypatch, bearer
):
    token = await sync_to_async(_make_token)(world) if bearer else None
    info = _info(world, token=token)
    calls = []
    created = []

    def add_rows():
        own = FormSubmission.objects.create(
            organization=world.org, form=world.form, form_version=1, payload={"own": "new"}
        )
        FormSubmission.objects.create(
            organization=world.other_org,
            form=world.foreign_form,
            form_version=1,
            payload={"foreign": "secret"},
        )
        FormSubmission.objects.create(
            organization=world.org, form=world.foreign_form, form_version=1, payload={"corrupt": "secret"}
        )
        FormSubmission.objects.create(
            organization=world.other_org,
            form=world.form,
            form_version=1,
            payload={"mismatched_owner": "secret"},
        )
        deleted = FormDefinition.objects.create(
            organization=world.org, name="Deleted", slug="deleted-stream-2112"
        )
        FormSubmission.objects.create(organization=world.org, form=deleted, form_version=1, payload={})
        deleted.soft_delete()
        return own.pk

    async def poll(_seconds):
        calls.append(True)
        assert len(calls) == 1
        created.append(await sync_to_async(add_rows)())

    monkeypatch.setattr("core.schema.subscriptions.asyncio.sleep", poll)
    handle = set_current_api_token(None)
    try:
        with tenant_context(_tenant_value(world, org=world.other_org)):
            stream = _CoreSubscription().form_submission_received(info, slug=world.form.slug)
            # The handshake identity and grants matter at first iteration.
            await sync_to_async(_grant)(world)
            try:
                assert await asyncio.wait_for(anext(stream), 5) == f"New submission #{created[0]}"
            finally:
                await stream.aclose()
            with pytest.raises(StopAsyncIteration):
                await anext(stream)
    finally:
        reset_current_api_token(handle)
    assert calls == [True]


@pytest.mark.parametrize("target", ["foreign", "deleted", "missing"])
async def test_authorized_stream_does_not_poll_missing_or_foreign_forms(world, monkeypatch, target):
    await sync_to_async(_grant)(world)
    slug = world.foreign_form.slug if target == "foreign" else world.form.slug
    if target == "deleted":
        await sync_to_async(world.form.soft_delete)()
    elif target == "missing":
        slug = "missing-stream-2112"

    async def must_not_poll(_seconds):
        pytest.fail("missing form started polling")

    monkeypatch.setattr("core.schema.subscriptions.asyncio.sleep", must_not_poll)
    assert await _drain(world, _info(world), slug) == []


async def test_cancelling_waiting_stream_closes_the_inner_poll(world, monkeypatch):
    await sync_to_async(_grant)(world)
    entered = asyncio.Event()
    release = asyncio.Event()
    closed = []

    async def waiting_poll(_seconds):
        entered.set()
        try:
            await release.wait()
        finally:
            closed.append(True)

    monkeypatch.setattr("core.schema.subscriptions.asyncio.sleep", waiting_poll)
    handle = set_current_api_token(None)
    try:
        with tenant_context(None):
            stream = _CoreSubscription().form_submission_received(_info(world), slug=world.form.slug)
            pending = asyncio.create_task(anext(stream))
            try:
                await asyncio.wait_for(entered.wait(), 5)
            finally:
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
                await stream.aclose()
            with pytest.raises(StopAsyncIteration):
                await anext(stream)
    finally:
        reset_current_api_token(handle)
    assert closed == [True]


async def test_deleted_form_cannot_emit_new_or_orphaned_submission_rows(world, monkeypatch):
    await sync_to_async(_grant)(world)

    class StopPolling(Exception):
        pass

    calls = []

    def deleted_rows():
        world.form.soft_delete()
        FormSubmission.objects.create(
            organization=world.org, form=world.form, form_version=1, payload={"deleted": "secret"}
        )
        replacement = FormDefinition.objects.create(
            organization=world.org, name="Replacement", slug=world.form.slug
        )
        FormSubmission.objects.create(
            organization=world.org, form=replacement, form_version=1, payload={"replacement": "secret"}
        )

    async def poll(_seconds):
        calls.append(True)
        if len(calls) == 1:
            await sync_to_async(deleted_rows)()
            return
        raise StopPolling

    monkeypatch.setattr("core.schema.subscriptions.asyncio.sleep", poll)
    handle = set_current_api_token(None)
    try:
        with tenant_context(None):
            stream = _CoreSubscription().form_submission_received(_info(world), slug=world.form.slug)
            try:
                with pytest.raises(StopPolling):
                    await asyncio.wait_for(anext(stream), 5)
            finally:
                await stream.aclose()
    finally:
        reset_current_api_token(handle)
    assert len(calls) == 2
