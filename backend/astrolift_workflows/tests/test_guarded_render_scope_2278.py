"""Actual Worker/GoogleAuth/owned transport controls for trusted added DB fences."""

from contextlib import contextmanager

import pytest

from astrolift_lifecycle.models import Deployment
from astrolift_workflows.tests import test_native_execution_checkpoint_2278 as controls

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def world(monkeypatch, client, tmp_path):
    w = controls.world.__wrapped__(monkeypatch, client, tmp_path)
    original = controls.deployment_execution_checkpoint

    @contextmanager
    def guarded(input):
        with original(input) as parent:

            def current_render_row():
                assert Deployment.objects.filter(pk=input.deployment_id).exists()

            child = parent.with_checks(current_render_row)
            assert type(child) is type(parent)
            assert child.closed is parent.closed and child.thread_scope is parent.thread_scope
            assert child.owner_thread == parent.owner_thread and child.context is parent.context
            yield child

    monkeypatch.setattr(controls, "deployment_execution_checkpoint", guarded)
    return w


@pytest.mark.slow
@pytest.mark.asyncio
@pytest.mark.parametrize("withdraw", ["reader", "reader_denied"])
async def test_added_render_fence_preserves_actual_reader_refresh_owned_connection_cleanup(
    world, client, temporal_env, monkeypatch, tmp_path, caplog, capsys, withdraw
):
    await controls.test_actual_google_auth_grpc_refresh_thread_checks_execution_before_http(
        world, client, temporal_env, monkeypatch, tmp_path, caplog, capsys, withdraw
    )


@pytest.mark.asyncio
async def test_added_render_fence_shares_closed_scope_for_owner_and_metadata_callbacks(
    world, client, temporal_env
):
    await controls.test_scoped_callback_refuses_after_exit_on_owner_and_metadata_thread(
        world, client, temporal_env
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("outer_atomic", [False, True])
async def test_added_render_fence_preserves_unrelated_alias_connection_and_transaction(
    world, client, temporal_env, outer_atomic, django_db_blocker
):
    await controls.test_metadata_cleanup_never_sweeps_unrelated_database_alias(
        world, client, temporal_env, outer_atomic, django_db_blocker
    )
