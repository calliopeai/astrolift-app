"""Registered native identity removal for app teardown (#2278)."""

import re

from temporalio import activity
from temporalio.exceptions import ApplicationError

from astrolift_workflows.activity_heartbeat import heartbeat_while

#: Prefix of every refusal; the teardown reports the removal pending with it.
PENDING = "NATIVE_IDENTITY_REMOVAL_PENDING:"
_FIXED_REASON = re.compile(r"[A-Z][A-Z0-9_]{2,63}")


def _server_ports():
    from astrolift_workflows.native_endpoint_configuration import NativeConfigurationPorts

    return NativeConfigurationPorts()


def _remove_sync(registered_app_id, workflow_id, execution_id):
    # Django-backed removal loads at call time; the workflow sandbox imports this module.
    from astrolift_workflows.native_endpoint_removal import remove_native_endpoint_identity as remove

    try:
        return remove(
            registered_app_id, workflow_id=workflow_id, execution_id=execution_id, ports=_server_ports()
        )
    except Exception as error:
        # Only fixed upper-case codes leave; native text never does.
        reason = str(error)
        raise ApplicationError(
            PENDING + (reason if _FIXED_REASON.fullmatch(reason) else "REMOVAL_UNCONFIRMED"),
            non_retryable=True,
        ) from None


@activity.defn(name="astrolift.app.remove_native_endpoint_identity")
async def remove_native_endpoint_identity(registered_app_id: int) -> list[dict]:
    """Revoke the grants the app's native GCP IAM journals own; pending until observed gone."""
    from asgiref.sync import sync_to_async

    info = activity.info()
    return await heartbeat_while(
        sync_to_async(_remove_sync)(registered_app_id, info.workflow_id, info.workflow_run_id)
    )
