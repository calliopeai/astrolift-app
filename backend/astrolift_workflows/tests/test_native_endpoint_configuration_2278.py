"""Actual HTTP enqueue/Worker/original receipts and generated SDK + owned TLS."""

import asyncio
import base64
import json
import re
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict
from datetime import timedelta
from threading import Event, get_ident
from threading import enumerate as active_threads
from types import SimpleNamespace
from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from gcp.gke_app_apply import GKEAppApply
from gcp.gke_identity_preparation import GKEIdentityPreparation
from gcp.identity_owned import NativeGCPIdentity
from google.iam.v1 import policy_pb2
from temporalio import workflow
from temporalio.api.common.v1.message_pb2 import Payload, Payloads
from temporalio.api.enums.v1 import EventType, TimeoutType
from temporalio.api.failure.v1.message_pb2 import Failure, TimeoutFailureInfo
from temporalio.api.history.v1.message_pb2 import (
    ActivityTaskCancelRequestedEventAttributes,
    ActivityTaskTimedOutEventAttributes,
    HistoryEvent,
    WorkflowExecutionCancelRequestedEventAttributes,
)
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, CancelledError
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker

from astrolift_lifecycle.models import DeploymentExecutionReceipt
from astrolift_services.models import (
    GCPGKEAppApplyJournal,
    GCPGKEPreparationJournal,
    GCPIdentityAcknowledgement,
    GCPWorkloadIdentityJournal,
    ManagedService,
)
from astrolift_services.tests.test_gcp_app_identity_plan_2278 import SourceWire
from astrolift_services.tests.test_gcp_deployment_context_2278 import dispatch_context
from astrolift_workflows.activities.native_endpoint_configuration import configure_native_endpoint_deployment
from astrolift_workflows.inputs import DeployAppInput
from astrolift_workflows.native_endpoint_configuration import (
    NativeConfigurationPorts,
    execute_native_endpoint_configuration,
)
from astrolift_workflows.tests.test_native_execution_checkpoint_2278 import world as original_world
from providers.tests.gcp.test_gke_app_apply_2278 import native as tls_fixture
from providers.tests.gcp.test_identity_submission_2278 import SubmissionWire

_HISTORY_SCAN_LIMIT = 2048
_HISTORY_ROW_LIMIT = 8
_HISTORY_COUNT_LIMIT = 1_000_000
_FAILURE_DIAGNOSTIC_BYTES = 16 * 1024
_FAILURE_HISTORY_SECONDS = 3
_OWNED_THREAD_FRAME_LIMIT = 12
_SAFE_TIMEOUT_NAMES = {
    "TIMEOUT_TYPE_START_TO_CLOSE",
    "TIMEOUT_TYPE_SCHEDULE_TO_START",
    "TIMEOUT_TYPE_SCHEDULE_TO_CLOSE",
    "TIMEOUT_TYPE_HEARTBEAT",
}
_SAFE_TERMINAL_EVENT_NAMES = {
    "EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED",
    "EVENT_TYPE_WORKFLOW_EXECUTION_FAILED",
    "EVENT_TYPE_WORKFLOW_EXECUTION_CANCELED",
    "EVENT_TYPE_WORKFLOW_EXECUTION_TIMED_OUT",
    "EVENT_TYPE_WORKFLOW_EXECUTION_TERMINATED",
    "EVENT_TYPE_WORKFLOW_EXECUTION_CONTINUED_AS_NEW",
}


def _safe_temporal_history_summary(history):
    """Return only bounded Temporal event codes/IDs, never payloads or messages."""
    events = getattr(history, "events", ())
    if not hasattr(events, "__iter__"):
        events = ()
    try:
        available = len(events)
    except (TypeError, OverflowError):
        available = 0
    available = min(max(available, 0), _HISTORY_COUNT_LIMIT)
    timeout_counts = dict.fromkeys(sorted(_SAFE_TIMEOUT_NAMES), 0)
    event_type_counts = dict.fromkeys(
        (
            "EVENT_TYPE_ACTIVITY_TASK_TIMED_OUT",
            "EVENT_TYPE_ACTIVITY_TASK_CANCEL_REQUESTED",
            "EVENT_TYPE_ACTIVITY_TASK_CANCELED",
            "EVENT_TYPE_WORKFLOW_EXECUTION_CANCEL_REQUESTED",
            *_SAFE_TERMINAL_EVENT_NAMES,
        ),
        0,
    )
    timeout_rows = []
    activity_cancel_request_rows = []
    cancellation_ids = []
    cancel_request_ids = []
    terminal_rows = []
    scanned = 0
    unclassified_event_count = 0
    unknown_event_enum_count = 0

    def safe_id(value):
        return value if type(value) is int and 0 <= value <= (2**63 - 1) else None

    def has_field(message, field_name):
        method = getattr(message, "HasField", None)
        if not callable(method):
            return False
        try:
            return method(field_name) is True
        except (AttributeError, TypeError, ValueError):
            return False

    for event in events:
        if scanned >= _HISTORY_SCAN_LIMIT:
            break
        scanned += 1
        event_type = getattr(event, "event_type", None)
        try:
            name = EventType.Name(event_type) if type(event_type) is int else None
        except (ValueError, TypeError):
            name = None
        event_id = safe_id(getattr(event, "event_id", None))
        if name in event_type_counts:
            event_type_counts[name] = min(event_type_counts[name] + 1, _HISTORY_COUNT_LIMIT)
        if name == "EVENT_TYPE_ACTIVITY_TASK_TIMED_OUT":
            attrs = getattr(event, "activity_task_timed_out_event_attributes", None)
            timeout_name = None
            if (
                attrs is not None
                and has_field(event, "activity_task_timed_out_event_attributes")
                and has_field(attrs, "failure")
            ):
                failure = attrs.failure
                if has_field(failure, "timeout_failure_info"):
                    timeout_failure = failure.timeout_failure_info
                    # timeout_type is a proto3 scalar without field presence;
                    # an unspecified/unknown enum is rejected by the whitelist below.
                    try:
                        timeout_type = timeout_failure.timeout_type
                        timeout_name = TimeoutType.Name(timeout_type) if type(timeout_type) is int else None
                    except (ValueError, TypeError):
                        timeout_name = None
            if timeout_name in timeout_counts:
                timeout_counts[timeout_name] = min(timeout_counts[timeout_name] + 1, _HISTORY_COUNT_LIMIT)
                if len(timeout_rows) < _HISTORY_ROW_LIMIT:
                    timeout_rows.append(
                        {
                            "eventId": event_id,
                            "timeoutType": timeout_name,
                            "scheduledEventId": safe_id(getattr(attrs, "scheduled_event_id", None)),
                            "startedEventId": safe_id(getattr(attrs, "started_event_id", None)),
                        }
                    )
            else:
                unclassified_event_count = min(unclassified_event_count + 1, _HISTORY_COUNT_LIMIT)
        elif name == "EVENT_TYPE_ACTIVITY_TASK_CANCEL_REQUESTED":
            attrs = getattr(event, "activity_task_cancel_requested_event_attributes", None)
            if attrs is not None and has_field(event, "activity_task_cancel_requested_event_attributes"):
                if len(activity_cancel_request_rows) < _HISTORY_ROW_LIMIT:
                    activity_cancel_request_rows.append(
                        {
                            "eventId": event_id,
                            "scheduledEventId": safe_id(getattr(attrs, "scheduled_event_id", None)),
                        }
                    )
            else:
                unclassified_event_count = min(unclassified_event_count + 1, _HISTORY_COUNT_LIMIT)
        elif name == "EVENT_TYPE_ACTIVITY_TASK_CANCELED":
            if len(cancellation_ids) < _HISTORY_ROW_LIMIT:
                cancellation_ids.append(event_id)
        elif name == "EVENT_TYPE_WORKFLOW_EXECUTION_CANCEL_REQUESTED":
            if len(cancel_request_ids) < _HISTORY_ROW_LIMIT:
                cancel_request_ids.append(event_id)
        elif name in _SAFE_TERMINAL_EVENT_NAMES and len(terminal_rows) < _HISTORY_ROW_LIMIT:
            terminal_rows.append({"eventId": event_id, "eventType": name})
        elif name is None:
            unknown_event_enum_count = min(unknown_event_enum_count + 1, _HISTORY_COUNT_LIMIT)

    return {
        "eventsAvailableCapped": available,
        "eventsScanned": scanned,
        "eventsTruncated": scanned < available,
        "unclassifiedEventCount": unclassified_event_count,
        "unknownEventEnumCount": unknown_event_enum_count,
        "eventTypeCounts": event_type_counts,
        "activityTimeoutCounts": timeout_counts,
        "activityTimeouts": timeout_rows,
        "activityCancelRequestEvents": activity_cancel_request_rows,
        "activityCancellationEventIds": cancellation_ids,
        "workflowCancelRequestEventIds": cancel_request_ids,
        "cancellationObserved": bool(
            activity_cancel_request_rows
            or cancellation_ids
            or cancel_request_ids
            or any(row["eventType"] == "EVENT_TYPE_WORKFLOW_EXECUTION_CANCELED" for row in terminal_rows)
        ),
        "workflowTerminalEvents": terminal_rows,
    }


def test_safe_temporal_history_summary_is_bounded_and_omits_payloads():
    timeout_event = HistoryEvent(
        event_id=6,
        event_type=EventType.EVENT_TYPE_ACTIVITY_TASK_TIMED_OUT,
        activity_task_timed_out_event_attributes=ActivityTaskTimedOutEventAttributes(
            scheduled_event_id=4,
            started_event_id=5,
            failure=Failure(
                message="private-failure-message-canary",
                timeout_failure_info=TimeoutFailureInfo(
                    timeout_type=TimeoutType.TIMEOUT_TYPE_HEARTBEAT,
                    last_heartbeat_details=Payloads(
                        payloads=[Payload(data=b"private-heartbeat-details-canary")]
                    ),
                ),
            ),
        ),
    )
    events = [
        timeout_event,
        HistoryEvent(
            event_type=EventType.EVENT_TYPE_ACTIVITY_TASK_CANCEL_REQUESTED,
            event_id=7,
            activity_task_cancel_requested_event_attributes=ActivityTaskCancelRequestedEventAttributes(
                scheduled_event_id=6,
                workflow_task_completed_event_id=8,
            ),
        ),
        HistoryEvent(
            event_type=EventType.EVENT_TYPE_WORKFLOW_EXECUTION_CANCEL_REQUESTED,
            event_id=8,
            workflow_execution_cancel_requested_event_attributes=WorkflowExecutionCancelRequestedEventAttributes(
                cause="private-cancel-cause-canary",
            ),
        ),
        *(
            HistoryEvent(event_type=EventType.EVENT_TYPE_TIMER_STARTED, event_id=index + 9)
            for index in range(_HISTORY_SCAN_LIMIT + 2)
        ),
    ]
    summary = _safe_temporal_history_summary(SimpleNamespace(events=events))
    encoded = json.dumps(summary, sort_keys=True)

    assert summary["eventsScanned"] == _HISTORY_SCAN_LIMIT
    assert summary["eventsTruncated"] is True
    assert summary["activityTimeoutCounts"]["TIMEOUT_TYPE_HEARTBEAT"] == 1
    assert summary["eventTypeCounts"]["EVENT_TYPE_ACTIVITY_TASK_TIMED_OUT"] == 1
    assert summary["eventTypeCounts"]["EVENT_TYPE_WORKFLOW_EXECUTION_CANCEL_REQUESTED"] == 1
    assert summary["cancellationObserved"] is True
    assert summary["activityTimeouts"] == [
        {"eventId": 6, "timeoutType": "TIMEOUT_TYPE_HEARTBEAT", "scheduledEventId": 4, "startedEventId": 5}
    ]
    assert summary["activityCancelRequestEvents"] == [{"eventId": 7, "scheduledEventId": 6}]
    assert summary["workflowCancelRequestEventIds"] == [8]
    assert len(encoded) < 4096
    assert "private-" not in encoded


def _safe_exception_chain(error):
    rows = []
    seen = set()
    current = error
    while isinstance(current, BaseException) and id(current) not in seen and len(rows) < 8:
        seen.add(id(current))
        exception_type = type(current).__name__
        if not re.fullmatch(r"[A-Za-z0-9_]{1,96}", exception_type):
            exception_type = "UnknownException"
        frame = current.__traceback__
        source_file, source_line = "unknown", 0
        for _ in range(128):
            if frame is None:
                break
            if frame.tb_next is None:
                candidate = frame.tb_frame.f_code.co_filename.rsplit("/", 1)[-1]
                if re.fullmatch(r"[A-Za-z0-9_.-]{1,96}", candidate):
                    source_file = candidate
                source_line = frame.tb_lineno
                break
            frame = frame.tb_next
        else:
            source_file = "truncated"
        rows.append({"exceptionType": exception_type, "sourceFile": source_file, "sourceLine": source_line})
        current = current.__cause__ if current.__cause__ is not None else current.__context__
    return rows


def test_safe_exception_chain_includes_suppressed_context_without_text():
    try:
        try:
            raise RuntimeError("private-inner-error-canary")
        except RuntimeError:
            raise ValueError("private-outer-error-canary") from None
    except ValueError as error:
        rows = _safe_exception_chain(error)

    assert [row["exceptionType"] for row in rows] == ["ValueError", "RuntimeError"]
    assert all(set(row) == {"exceptionType", "sourceFile", "sourceLine"} for row in rows)
    assert all(row["sourceLine"] > 0 for row in rows)
    assert "private-" not in json.dumps(rows)

    chain = RuntimeError("private-cycle-canary")
    chain.__context__ = chain
    cycle_rows = _safe_exception_chain(chain)
    assert len(cycle_rows) == 1 and cycle_rows[0]["exceptionType"] == "RuntimeError"

    deep = [RuntimeError(f"private-depth-{index}") for index in range(10)]
    for current, following in zip(deep[:-1], deep[1:], strict=True):
        current.__context__ = following
    deep_rows = _safe_exception_chain(deep[0])
    assert len(deep_rows) == 8
    assert "private-depth" not in json.dumps(deep_rows)


def _safe_owned_thread_stack(thread_id):
    """Read frames for one previously registered thread, never locals or other threads."""
    if type(thread_id) is not int or thread_id <= 0:
        return []
    frame = sys._current_frames().get(thread_id)
    rows = []
    while frame is not None and len(rows) < _OWNED_THREAD_FRAME_LIMIT:
        filename = frame.f_code.co_filename.rsplit("/", 1)[-1]
        function = frame.f_code.co_name
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,96}", filename):
            filename = "unknown"
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,96}", function):
            function = "unknown"
        line = frame.f_lineno
        rows.append(
            {"file": filename, "function": function, "line": line if type(line) is int and line > 0 else 0}
        )
        frame = frame.f_back
    return rows


def _safe_checkpoint_summary(world):
    stage_names = {
        "direct_db_transition",
        "source_bootstrap",
        "endpoint_source",
        "endpoint_refresh",
        "preparation",
        "annotation",
        "iam",
        "placement",
        "apply",
        "runtime",
    }
    stages = {}
    for name, count in getattr(world, "checkpoint_stages", {}).items():
        if name in stage_names and type(count) is int and 0 <= count <= _HISTORY_COUNT_LIMIT:
            stages[name] = count
    calls = getattr(world, "checkpoint_calls", 0)
    return {
        "calls": calls if type(calls) is int and 0 <= calls <= _HISTORY_COUNT_LIMIT else 0,
        "stages": stages,
    }


def _failure_diagnostic_line(*, callback_error, thread_frames, checkpoints, history=None, history_error=None):
    callback_chain = _safe_exception_chain(callback_error)
    history_error_chain = (
        _safe_exception_chain(history_error) if isinstance(history_error, BaseException) else []
    )
    safe_frames = []
    for row in thread_frames[:_OWNED_THREAD_FRAME_LIMIT]:
        if not isinstance(row, dict):
            continue
        filename = row.get("file")
        function = row.get("function")
        line = row.get("line")
        if not isinstance(filename, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,96}", filename):
            filename = "unknown"
        if not isinstance(function, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,96}", function):
            function = "unknown"
        safe_frames.append(
            {
                "file": filename,
                "function": function,
                "line": line if type(line) is int and 0 < line < 10**7 else 0,
            }
        )
    value = {
        "callbackError": callback_chain,
        "activityThreadFrames": safe_frames,
        "checkpoints": checkpoints,
        "history": history,
        "historyDiagnosticError": history_error_chain,
    }
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
        if len(encoded.encode("utf-8")) <= _FAILURE_DIAGNOSTIC_BYTES:
            return encoded
    except BaseException:
        pass
    # Diagnostics are best-effort and must never replace the callback failure.
    return '{"diagnostic":"bounded_failure_diagnostic_unavailable"}'


def test_failure_diagnostic_is_bounded_and_redacts_callback_and_history_payloads():
    callback_error = None
    try:
        raise RuntimeError("private-callback-message-canary")
    except RuntimeError as error:
        callback_error = error
    history = _safe_temporal_history_summary(
        SimpleNamespace(
            events=[
                HistoryEvent(
                    event_id=1,
                    event_type=EventType.EVENT_TYPE_WORKFLOW_EXECUTION_FAILED,
                )
            ]
        )
    )
    line = _failure_diagnostic_line(
        callback_error=callback_error,
        thread_frames=[{"file": "worker.py", "function": "run", "line": 12}] * 5000,
        checkpoints={"calls": 3, "stages": {"apply": 1}},
        history=history,
        history_error=ValueError("private-history-message-canary"),
    )
    parsed = json.loads(line)

    assert len(line.encode("utf-8")) <= _FAILURE_DIAGNOSTIC_BYTES
    assert len(parsed["activityThreadFrames"]) <= _OWNED_THREAD_FRAME_LIMIT
    assert "private-" not in line
    assert "callbackErrorType" not in parsed


def test_failure_diagnostic_formatter_failure_has_constant_safe_fallback(monkeypatch):
    try:
        raise RuntimeError("private-callback-message-canary")
    except RuntimeError as error:
        callback_error = error

    monkeypatch.setattr(json, "dumps", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("secret")))
    line = _failure_diagnostic_line(
        callback_error=callback_error,
        thread_frames=[],
        checkpoints={"calls": 0, "stages": {}},
    )

    assert line == '{"diagnostic":"bounded_failure_diagnostic_unavailable"}'


def test_owned_thread_stack_reads_only_registered_thread_and_caps_frames(monkeypatch):
    class Frame:
        f_back = None
        f_lineno = 7
        f_code = SimpleNamespace(co_filename="/private/worker.py", co_name="owned")

    frame = Frame()
    monkeypatch.setattr(sys, "_current_frames", lambda: {41: frame, 42: Frame()})
    rows = _safe_owned_thread_stack(41)

    assert rows == [{"file": "worker.py", "function": "owned", "line": 7}]
    assert _safe_owned_thread_stack(0) == []


async def _fetch_bounded_failure_history(handle):
    events = []
    iterator = handle.fetch_history_events(
        page_size=64,
        rpc_timeout=timedelta(seconds=1),
    ).__aiter__()
    for _ in range(_HISTORY_SCAN_LIMIT):
        try:
            event = await iterator.__anext__()
        except StopAsyncIteration:
            break
        events.append(event)
    limit_reached = len(events) == _HISTORY_SCAN_LIMIT
    summary = _safe_temporal_history_summary(SimpleNamespace(events=events))
    summary["inputEventLimitReached"] = limit_reached
    summary["eventsTotalKnown"] = not limit_reached
    if limit_reached:
        # The iterator was intentionally not advanced to discover whether the
        # history ended exactly at the cap; report a possibly truncated prefix.
        summary["eventsTruncated"] = True
    return summary


@pytest.mark.asyncio
async def test_bounded_failure_history_marks_limit_without_fetching_an_extra_event():
    class Iterator:
        def __init__(self):
            self.consumed = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.consumed >= _HISTORY_SCAN_LIMIT + 2:
                raise StopAsyncIteration
            event = SimpleNamespace(
                event_id=self.consumed + 1,
                event_type=EventType.EVENT_TYPE_TIMER_STARTED,
                payload="private-event-content-canary",
            )
            self.consumed += 1
            return event

    class Handle:
        def __init__(self):
            self.iterator = Iterator()
            self.arguments = None

        def fetch_history_events(self, **kwargs):
            self.arguments = kwargs
            return self.iterator

    handle = Handle()
    summary = await _fetch_bounded_failure_history(handle)

    assert handle.arguments == {"page_size": 64, "rpc_timeout": timedelta(seconds=1)}
    assert handle.iterator.consumed == _HISTORY_SCAN_LIMIT
    assert summary["eventsScanned"] == _HISTORY_SCAN_LIMIT
    assert summary["inputEventLimitReached"] is True
    assert summary["eventsTotalKnown"] is False
    assert summary["eventsTruncated"] is True
    assert "private-event-content-canary" not in json.dumps(summary)


pytestmark = pytest.mark.django_db(transaction=True)


@workflow.defn(name="DeployAppWorkflow")
class ConfigurationWorkflow:
    @workflow.run
    async def run(self, input: DeployAppInput) -> dict:
        try:
            result = await workflow.execute_activity(
                configure_native_endpoint_deployment,
                input,
                start_to_close_timeout=timedelta(seconds=900),
                heartbeat_timeout=timedelta(seconds=2),
                cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
                retry_policy=RetryPolicy(maximum_attempts=1),
            )
            return asdict(result)
        except ActivityError:
            return {"refused": True}
        except CancelledError:
            return {"cancelled": True}


@workflow.defn(name="DeployAppWorkflow")
class WithoutHeartbeatWorkflow:
    @workflow.run
    async def run(self, input: DeployAppInput) -> dict:
        try:
            await workflow.execute_activity(
                configure_native_endpoint_deployment,
                input,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=RetryPolicy(maximum_attempts=1),
            )
        except ActivityError:
            return {"refused": True}
        return {"unexpectedly_accepted": True}


@workflow.defn(name="DeployAppWorkflow")
class SameRunRecoveryWorkflow:
    @workflow.run
    async def run(self, input: DeployAppInput) -> dict:
        attempts = []
        for _ in range(2):
            try:
                result = await workflow.execute_activity(
                    configure_native_endpoint_deployment,
                    input,
                    start_to_close_timeout=timedelta(seconds=900),
                    heartbeat_timeout=timedelta(seconds=2),
                    retry_policy=RetryPolicy(maximum_attempts=1),
                )
                attempts.append(asdict(result))
            except ActivityError:
                attempts.append({"refused": True})
        return {"attempts": attempts}


@pytest.fixture
def native_world(monkeypatch, client, tmp_path):
    w = original_world.__wrapped__(monkeypatch, client, tmp_path)
    gen = tls_fixture.__wrapped__(tmp_path, monkeypatch)
    driver, _, http, wire = next(gen)
    w.cluster.endpoint = wire.cluster.endpoint
    w.cluster.ca_cert = wire.cluster.master_auth.cluster_ca_certificate
    w.cluster.save()
    w.native.cluster.endpoint = w.cluster.endpoint
    w.native.cluster.master_auth.cluster_ca_certificate = w.cluster.ca_cert
    w.native.cluster.name = wire.cluster.name
    w.native.cluster.id = wire.cluster.id
    w.native.cluster.location = wire.cluster.location
    w.medops_app.manifest_raw = "\n".join(
        (
            'name = "fixture"',
            "[[workloads]]",
            'name = "web"',
            'kind = "deployment"',
            "replicas = 1",
            "[[workloads.containers]]",
            'name = "web"',
            "is_primary = true",
            "port = 8080",
        )
    )
    w.medops_app.save()
    w.http, w.wire = http, wire
    wire.cluster.locations = [wire.cluster.location]
    wire.pools.node_pools[0].locations = [wire.cluster.location]
    w.monkeypatch = monkeypatch
    monkeypatch.setattr(
        "providers.tests.gcp.test_identity_owned_2278.ROLE",
        w.cluster.provider_config["endpoint_prediction_role"],
    )
    http["before_write"] = None
    w.endpoint_wires, w.iam_wires = [], []

    def source(declaration):
        return w.native.port(declaration)

    def endpoints(identity, *, checkpoint):
        endpoint_wire = SourceWire(identity, w.source.endpoint)
        w.endpoint_wires.append(endpoint_wire)
        return endpoint_wire.observer(identity, checkpoint=checkpoint)

    def preparation(context):
        wire.cluster.name, wire.cluster.id, wire.cluster.location = (
            context.cluster_name,
            context.native_cluster_id,
            context.location,
        )
        value = GKEIdentityPreparation(context, clients=wire.clients)
        value.observer._credentials = driver.observer._credentials
        return value

    def iam(identity):
        value = SubmissionWire()
        value.account.name = f"projects/{identity.project_id}/serviceAccounts/{identity.email}"
        value.account.email = identity.email
        value.account.unique_id = identity.service_account_unique_id
        value.account.description = identity.owner_description
        value.role.name = w.cluster.provider_config["endpoint_prediction_role"]
        resource = "projects/" + identity.project_number + "/" + w.native.endpoint.name.split("/", 2)[2]
        value.policies = {
            name: policy_pb2.Policy(version=3, etag=b"initial")
            for name in (identity.service_account_resource, resource)
        }
        w.iam_wires.append(value)
        return NativeGCPIdentity(identity, clients=value.clients)

    def apply(context):
        value = GKEAppApply(context, clients=wire.clients)
        value.observer._credentials = driver.observer._credentials
        return value

    w.failures = []
    w.after_refusal = None
    w.before_ports = None
    w.checkpoint_calls = 0
    w.checkpoint_stages = {}
    from astrolift_lifecycle.deployment_execution_checkpoint import _ScopedDeploymentExecutionCheckpoint

    original_admitted = _ScopedDeploymentExecutionCheckpoint._admitted

    def measured_admitted(self):
        w.checkpoint_calls += 1
        frame = sys._getframe()
        stage = "direct_db_transition"
        names = {
            "bootstrap_original_identity": "source_bootstrap",
            "produce_endpoint_app_plan": "endpoint_source",
            "refresh_endpoint_app_sources": "endpoint_refresh",
            "prepare": "preparation",
            "annotate": "annotation",
            "reconcile": "iam",
            "capture_placement": "placement",
            "apply": "apply",
            "observe_handoff_runtime": "runtime",
        }
        while frame:
            if frame.f_code.co_name in names:
                stage = names[frame.f_code.co_name]
                break
            frame = frame.f_back
        w.checkpoint_stages[stage] = w.checkpoint_stages.get(stage, 0) + 1
        return original_admitted(self)

    monkeypatch.setattr(_ScopedDeploymentExecutionCheckpoint, "_admitted", measured_admitted)

    def recorded(input, *, ports):
        w.activity_thread_id = get_ident()
        try:
            return execute_native_endpoint_configuration(input, ports=ports)
        except Exception as error:
            reason = str(error)
            w.frames = [(row.name, row.lineno) for row in traceback.extract_tb(error.__traceback__)]
            w.failure_causes = _safe_exception_chain(error)
            w.failures.append(
                (
                    type(error).__name__,
                    reason if re.fullmatch("[A-Z_]+", reason) else "PRIVATE_DIAGNOSTIC_SUPPRESSED",
                )
            )
            if w.after_refusal is not None:
                w.after_refusal()
            raise

    monkeypatch.setattr(
        "astrolift_workflows.activities.native_endpoint_configuration.execute_native_endpoint_configuration",
        recorded,
    )
    ports = NativeConfigurationPorts(source, endpoints, preparation, iam, apply)
    w.ports = ports
    from django.db import connection
    from gcp import gke_identity_preparation

    actual_http = gke_identity_preparation._http
    w.preparation_http_calls = 0

    def outside_transaction(*args, **kwargs):
        assert not connection.in_atomic_block and connection.get_autocommit()
        w.preparation_http_calls += 1
        return actual_http(*args, **kwargs)

    monkeypatch.setattr(gke_identity_preparation, "_http", outside_transaction)
    monkeypatch.setattr(
        "astrolift_workflows.activities.native_endpoint_configuration._server_ports", lambda: ports
    )

    def after_write(method, path):
        row = http["objects"][path]
        if row["kind"] == "Deployment":
            row["metadata"]["generation"] = 1
            row["status"] = {
                "observedGeneration": 1,
                "replicas": 1,
                "readyReplicas": 1,
                "updatedReplicas": 1,
                "availableReplicas": 1,
            }
            namespace, name = row["metadata"]["namespace"], row["metadata"]["name"]
            labels = deepcopy(row["spec"]["template"]["metadata"]["labels"])
            labels["pod-template-hash"] = "fixture-hash"
            child_uid, pod_uid, node_uid = (str(uuid4()) for _ in range(3))
            child = {
                "apiVersion": "apps/v1",
                "kind": "ReplicaSet",
                "metadata": {
                    "name": name + "-rs",
                    "namespace": namespace,
                    "uid": child_uid,
                    "resourceVersion": "fixture-rv",
                    "generation": 1,
                    "labels": labels,
                    "ownerReferences": [
                        {
                            "apiVersion": "apps/v1",
                            "kind": "Deployment",
                            "name": name,
                            "uid": row["metadata"]["uid"],
                            "controller": True,
                        }
                    ],
                },
                "spec": {"replicas": 1, "template": deepcopy(row["spec"]["template"])},
                "status": {"observedGeneration": 1, "replicas": 1, "readyReplicas": 1},
            }
            child["spec"]["template"]["metadata"]["labels"] = labels
            pod = {
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {
                    "name": name + "-pod",
                    "namespace": namespace,
                    "uid": pod_uid,
                    "resourceVersion": "fixture-rv",
                    "labels": labels,
                    "ownerReferences": [
                        {
                            "apiVersion": "apps/v1",
                            "kind": "ReplicaSet",
                            "name": name + "-rs",
                            "uid": child_uid,
                            "controller": True,
                        }
                    ],
                },
                "spec": {**deepcopy(row["spec"]["template"]["spec"]), "nodeName": "owned-node"},
                "status": {
                    "phase": "Running",
                    "conditions": [{"type": "Ready", "status": "True"}],
                    "containerStatuses": [
                        {"name": c["name"], "ready": True, "state": {"running": {}}}
                        for c in row["spec"]["template"]["spec"]["containers"]
                    ],
                },
            }
            http["objects"][f"/apis/apps/v1/namespaces/{namespace}/replicasets"] = {
                "apiVersion": "apps/v1",
                "kind": "ReplicaSetList",
                "metadata": {"resourceVersion": "fixture-rv"},
                "items": [child],
            }
            http["objects"][f"/apis/apps/v1/namespaces/{namespace}/replicasets?limit=257"] = http["objects"][
                f"/apis/apps/v1/namespaces/{namespace}/replicasets"
            ]
            http["objects"][f"/api/v1/namespaces/{namespace}/pods"] = {
                "apiVersion": "v1",
                "kind": "PodList",
                "metadata": {"resourceVersion": "fixture-rv"},
                "items": [pod],
            }
            http["objects"][f"/api/v1/namespaces/{namespace}/pods?limit=257"] = http["objects"][
                f"/api/v1/namespaces/{namespace}/pods"
            ]
            http["objects"]["/api/v1/nodes/owned-node"] = {
                "apiVersion": "v1",
                "kind": "Node",
                "metadata": {
                    "name": "owned-node",
                    "uid": node_uid,
                    "resourceVersion": "fixture-rv",
                    "labels": {
                        "cloud.google.com/gke-nodepool": "owned-pool",
                        "topology.kubernetes.io/zone": wire.cluster.location,
                        "iam.gke.io/gke-metadata-server-enabled": "true",
                    },
                },
                "spec": {"providerID": f"gce://{wire.project.project_id}/{wire.cluster.location}/owned-node"},
                "status": {"conditions": [{"type": "Ready", "status": "True"}]},
            }

    http["after_write"] = after_write
    try:
        yield w
    finally:
        try:
            next(gen)
        except StopIteration:
            pass


async def run(
    w,
    client,
    temporal_env,
    *,
    before=None,
    during=None,
    hold_activity=False,
    workflow_type=ConfigurationWorkflow,
):
    started = time.monotonic()
    from astrolift_lifecycle.schema.mutations import helpers
    from astrolift_workflows import client as native_client

    queue = "native-configuration-" + uuid4().hex
    released = Event()

    async def current_client():
        return temporal_env.client

    def actual_start(name, args, **kwargs):
        result = native_client.start_workflow(name, args, **kwargs)
        assert result.enqueued
        w.sdk_run_id = result.run_id
        w.starts.append(args[0])
        return result

    def scheduled_ports():
        w.activity_thread_id = get_ident()
        assert released.wait(30), "HTTP admission/controlled withdrawal must settle"
        if w.before_ports is not None:
            w.before_ports()
        return w.ports

    w.monkeypatch.setattr(native_client, "_get_client_async", current_client)
    w.monkeypatch.setattr(native_client, "_temporal_enabled", lambda: True)
    w.monkeypatch.setattr(native_client, "_task_queue", lambda: queue)
    w.monkeypatch.setattr(helpers, "start_workflow", actual_start)
    w.monkeypatch.setattr(
        "astrolift_workflows.activities.native_endpoint_configuration._server_ports", scheduled_ports
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        async with Worker(
            temporal_env.client,
            task_queue=queue,
            workflows=[workflow_type],
            activities=[configure_native_endpoint_deployment],
            activity_executor=executor,
            workflow_runner=UnsandboxedWorkflowRunner(),
        ):
            try:
                row, _, _ = await sync_to_async(dispatch_context)(w, client)
                expected = await sync_to_async(DeploymentExecutionReceipt.objects.get)(
                    deployment=row, kind="EXPECTED"
                )
                if before:
                    await sync_to_async(before)()
            finally:
                if not hold_activity:
                    released.set()
            handle = temporal_env.client.get_workflow_handle(expected.workflow_id, run_id=w.sdk_run_id)
            if during:
                try:
                    await during(handle)
                except BaseException as callback_error:
                    # Capture the one registered activity thread while it is
                    # still held, then release it before diagnostic I/O.
                    try:
                        activity_frames = _safe_owned_thread_stack(getattr(w, "activity_thread_id", None))
                    except BaseException:
                        activity_frames = []
                    try:
                        checkpoints = _safe_checkpoint_summary(w)
                    except BaseException:
                        checkpoints = {"calls": 0, "stages": {}}
                    released.set()
                    history_summary = None
                    history_error = None
                    try:
                        history_summary = await asyncio.wait_for(
                            _fetch_bounded_failure_history(handle),
                            timeout=_FAILURE_HISTORY_SECONDS,
                        )
                    except BaseException as diagnostic_error:
                        history_error = diagnostic_error
                    try:
                        print(
                            _failure_diagnostic_line(
                                callback_error=callback_error,
                                thread_frames=activity_frames,
                                checkpoints=checkpoints,
                                history=history_summary,
                                history_error=history_error,
                            )
                        )
                    except BaseException:
                        # The original callback failure remains authoritative.
                        pass
                    raise
                finally:
                    released.set()
            try:
                result = await handle.result()
            except Exception:
                print(
                    json.dumps(
                        {
                            "safe_failures": w.failures,
                            "safe_frames": getattr(w, "frames", []),
                            "safe_failure_causes": getattr(w, "failure_causes", []),
                        }
                    )
                )
                raise
            history = await handle.fetch_history()
    await Replayer(workflows=[workflow_type], workflow_runner=UnsandboxedWorkflowRunner()).replay_workflow(
        history
    )
    assert w.headers["HTTP_AUTHORIZATION"] not in history.to_json()
    assert "private-manifest-canary" not in history.to_json()
    assert not any(thread.name == "native-configuration-heartbeat" for thread in active_threads())
    print(
        json.dumps(
            {
                "execution_checkpoints": w.checkpoint_calls,
                "checkpoint_stages": w.checkpoint_stages,
                "native_request_counts": {
                    "source_sdk": len(w.native.calls),
                    "endpoint_sdk": sum(len(value.calls) for value in w.endpoint_wires),
                    "iam_sdk": sum(len(value.calls) for value in w.iam_wires),
                    "gke_sdk": len(w.wire.calls),
                    "kubernetes_http": len(w.http["requests"]),
                },
                "elapsed_seconds": round(time.monotonic() - started, 3),
            }
        )
    )
    return result, history


@pytest.mark.slow
@pytest.mark.asyncio
async def test_actual_registered_configuration_worker_commits_every_stage_and_returns_metadata_only(
    native_world, client, temporal_env
):
    w = native_world
    result, history = await run(w, client, temporal_env)
    assert not result.get("refused"), (w.failures, getattr(w, "frames", []))
    # Rollout is observed; token exchange and prediction are not.
    assert result["configuration_observed"] and result["workload_ready"] and not result["invocation_verified"]
    for cls in (GCPGKEPreparationJournal, GCPWorkloadIdentityJournal, GCPGKEAppApplyJournal):
        assert await sync_to_async(lambda cls=cls: cls.objects.get().state)() == "OBSERVED"
    assert await sync_to_async(GCPIdentityAcknowledgement.objects.count)() >= 5
    assert w.native.writes == 1
    assert w.preparation_http_calls > 0
    assert any(path.endswith("/pods?limit=257") for _, path, _ in w.http["requests"]), result[
        "runtime_observation_reason"
    ]
    assert any(path == "/api/v1/nodes/owned-node" for _, path, _ in w.http["requests"])
    assert result["runtime_observation_reason"] == "WORKLOAD_ROLLOUT_OBSERVED"
    assert any(path.endswith("/deployments/web") for _, path, _ in w.http["effects"])
    secret = next(row for row in w.http["objects"].values() if row["kind"] == "Secret")
    assert base64.b64decode(secret["data"]["MODEL_AUTH_MODE"]) == b"cloud_identity"
    controller = next(row for row in w.http["objects"].values() if row["kind"] == "Deployment")
    assert {"secretRef": {"name": secret["metadata"]["name"]}} in controller["spec"]["template"]["spec"][
        "containers"
    ][0]["envFrom"]
    serialized = json.dumps(result)
    assert "stringData" not in serialized and "ModelEndpoint" not in serialized


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "withdrawal",
    [
        "token",
        "terminal",
        "mixed",
        "saved_literal",
        "malformed_snapshot",
        "malformed_literals",
        "zero_replicas",
    ],
)
async def test_current_original_withdrawal_and_mixed_union_refuse_before_native_effects(
    native_world, client, temporal_env, withdrawal
):
    w = native_world

    def before():
        if withdrawal == "token":
            type(w.token).objects.filter(pk=w.token.pk).update(is_revoked=True)
        elif withdrawal == "terminal":
            from astrolift_lifecycle.models import Deployment

            Deployment.objects.filter(pk=w.starts[0].deployment_id).update(status="superseded")
        elif withdrawal in ("saved_literal", "malformed_snapshot", "malformed_literals"):
            from astrolift_lifecycle.models import Deployment

            snapshot = {
                "saved_literal": {"literals": {"PRIVATE_MARKER": "private-manifest-canary"}},
                "malformed_snapshot": ["private-manifest-canary"],
                "malformed_literals": {"literals": "private-manifest-canary"},
            }[withdrawal]
            Deployment.objects.filter(pk=w.starts[0].deployment_id).update(secret_snapshot=snapshot)
        elif withdrawal == "zero_replicas":
            type(w.medops_app).objects.filter(pk=w.medops_app.pk).update(
                manifest_raw=w.medops_app.manifest_raw.replace("replicas = 1", "replicas = 0")
            )
        else:
            ManagedService.objects.create(
                registered_app=w.medops_app, app_environment=w.env, kind="postgres", name="unsupported"
            )

    result, _ = await run(w, client, temporal_env, before=before)
    assert result == {"refused": True}
    assert not w.native.writes and not w.http["effects"] and not w.iam_wires
    assert await sync_to_async(GCPGKEPreparationJournal.objects.count)() == 0


@pytest.mark.asyncio
async def test_actual_namespace_reply_retains_original_uid_after_token_withdrawal(
    native_world, client, temporal_env
):
    w = native_world
    from django.db import connections

    original = w.http["after_write"]
    namespace_uids = []

    def withdraw_after_create(method, path):
        original(method, path)
        if method == "POST" and w.http["objects"][path]["kind"] == "Namespace":
            namespace_uids.append(w.http["objects"][path]["metadata"]["uid"])
            try:
                type(w.token).objects.filter(pk=w.token.pk).update(is_revoked=True)
            finally:
                # This server thread owns its fresh default connection only.
                connections["default"].close()

    w.http["after_write"] = withdraw_after_create
    result, _ = await run(w, client, temporal_env)
    assert result == {"refused": True}
    assert len(namespace_uids) == 1 and len(w.http["effects"]) == 1
    ack = await sync_to_async(GCPIdentityAcknowledgement.objects.get)(kind="PREPARATION")
    assert ack.acknowledgement["uid"] == namespace_uids[0]
    journal = await sync_to_async(GCPGKEPreparationJournal.objects.get)()
    assert journal.state == "SENT" and journal.prepared_revision == journal.observed_revision == 0
    assert all(value["state"] == "SENT" for value in journal.submissions.values())
    assert not w.iam_wires
    assert await sync_to_async(GCPWorkloadIdentityJournal.objects.count)() == 0
    assert await sync_to_async(GCPGKEAppApplyJournal.objects.count)() == 0


@pytest.mark.asyncio
async def test_lost_namespace_reply_same_actual_run_refuses_name_adoption(native_world, client, temporal_env):
    w = native_world
    original = w.http["after_write"]

    def lose_namespace_reply(method, path):
        original(method, path)
        if method == "POST" and w.http["objects"][path]["kind"] == "Namespace":
            w.http["lost"] = True

    w.http["after_write"] = lose_namespace_reply
    result, _ = await run(w, client, temporal_env, workflow_type=SameRunRecoveryWorkflow)
    assert result == {"attempts": [{"refused": True}, {"refused": True}]}
    assert len(w.http["effects"]) == 1
    assert await sync_to_async(GCPIdentityAcknowledgement.objects.count)() == 0
    journal = await sync_to_async(GCPGKEPreparationJournal.objects.get)()
    assert journal.state == "SENT" and journal.prepared_revision == journal.observed_revision == 0
    assert not w.iam_wires
    assert await sync_to_async(GCPWorkloadIdentityJournal.objects.count)() == 0
    assert await sync_to_async(GCPGKEAppApplyJournal.objects.count)() == 0
    bound = await sync_to_async(DeploymentExecutionReceipt.objects.get)(kind="BOUND")
    assert bound.run_id == w.sdk_run_id


@pytest.mark.asyncio
async def test_missing_heartbeat_contract_refuses_before_bound_or_native_effect(
    native_world, client, temporal_env
):
    w = native_world
    result, _ = await run(w, client, temporal_env, workflow_type=WithoutHeartbeatWorkflow)
    assert result == {"refused": True}
    assert not w.native.calls and not w.http["requests"]
    assert await sync_to_async(DeploymentExecutionReceipt.objects.filter(kind="BOUND").count)() == 0
    assert await sync_to_async(GCPIdentityAcknowledgement.objects.count)() == 0


@pytest.mark.asyncio
async def test_actual_cancel_delivered_before_held_reply_release_preserves_ack_only(
    native_world, client, temporal_env
):
    w = native_world
    from temporalio import activity

    created, released, observed_cancel = Event(), Event(), Event()
    original_write = w.http["after_write"]
    real_is_cancelled = activity.is_cancelled
    namespace_uids = []

    def observe_actual_cancel():
        value = real_is_cancelled()
        if value:
            observed_cancel.set()
        return value

    w.monkeypatch.setattr(activity, "is_cancelled", observe_actual_cancel)

    def hold_reply(method, path):
        original_write(method, path)
        if method == "POST" and w.http["objects"][path]["kind"] == "Namespace":
            namespace_uids.append(w.http["objects"][path]["metadata"]["uid"])
            created.set()
            assert released.wait(8), "owned native reply must remain bounded"

    async def cancel_before_reply(handle):
        assert await sync_to_async(created.wait)(120)
        try:
            description = await handle.describe()
            assert description.raw_description.pending_activities
            assert all(
                not row.heartbeat_details.payloads for row in description.raw_description.pending_activities
            )
            await handle.cancel()
            # This observes the real activity SDK flag while both Worker and
            # original request remain live, before any shutdown cancellation.
            assert await sync_to_async(observed_cancel.wait)(5)
        finally:
            released.set()

    w.http["after_write"] = hold_reply
    result, history = await run(w, client, temporal_env, during=cancel_before_reply)
    assert result in ({"cancelled": True}, {"refused": True})
    assert observed_cancel.is_set() and len(namespace_uids) == 1
    assert len(w.http["effects"]) == 1 and not w.iam_wires
    ack = await sync_to_async(GCPIdentityAcknowledgement.objects.get)(kind="PREPARATION")
    assert ack.acknowledgement["uid"] == namespace_uids[0]
    journal = await sync_to_async(GCPGKEPreparationJournal.objects.get)()
    assert journal.state == "SENT" and journal.prepared_revision == journal.observed_revision == 0
    assert await sync_to_async(GCPWorkloadIdentityJournal.objects.count)() == 0
    assert await sync_to_async(GCPGKEAppApplyJournal.objects.count)() == 0
    # Heartbeats carry no control data, invocation arguments or forensic body.
    for event in history.events:
        if event.HasField("activity_task_failed_event_attributes"):
            details = event.activity_task_failed_event_attributes.failure
            assert "private-manifest-canary" not in str(details)


@pytest.mark.asyncio
async def test_actual_cancel_before_ports_release_has_no_native_effect(native_world, client, temporal_env):
    w = native_world
    from temporalio import activity

    cancelled = Event()
    real_is_cancelled = activity.is_cancelled

    def observe_actual_cancel():
        value = real_is_cancelled()
        if value:
            cancelled.set()
        return value

    w.monkeypatch.setattr(activity, "is_cancelled", observe_actual_cancel)

    async def cancel_while_ports_wait(handle):
        await handle.cancel()
        assert await sync_to_async(cancelled.wait)(5)

    result, _ = await run(w, client, temporal_env, during=cancel_while_ports_wait, hold_activity=True)
    assert result in ({"cancelled": True}, {"refused": True})
    assert not w.native.calls and not w.http["requests"]
    assert await sync_to_async(DeploymentExecutionReceipt.objects.filter(kind="BOUND").count)() == 0


@pytest.mark.slow
@pytest.mark.asyncio
async def test_restored_current_policy_requires_fresh_original_uid_observation_on_same_run(
    native_world, client, temporal_env
):
    w = native_world
    from django.db import connections

    from astrolift_identity.models import Policy
    from astrolift_services.tests.test_gcp_deployment_context_2278 import approval_policy

    policies = []
    original_write = w.http["after_write"]
    namespace_uids, retained_before_restore = [], []

    def withdraw_policy_after_create(method, path):
        original_write(method, path)
        if method == "POST" and w.http["objects"][path]["kind"] == "Namespace":
            namespace_uids.append(w.http["objects"][path]["metadata"]["uid"])
            try:
                Policy.objects.filter(pk=policies[0].pk).update(
                    conditions=[{"kind": "approval_required", "min_approvers": 2}]
                )
            finally:
                connections["default"].close()

    def restore_original_policy():
        ack = GCPIdentityAcknowledgement.objects.get(kind="PREPARATION")
        journal = GCPGKEPreparationJournal.objects.get()
        assert journal.state == "SENT" and journal.prepared_revision == journal.observed_revision == 0
        assert ack.acknowledgement["uid"] == namespace_uids[0]
        retained_before_restore.append(ack.acknowledgement_sha256)
        Policy.objects.filter(pk=policies[0].pk).update(
            conditions=[{"kind": "approval_required", "min_approvers": 1}]
        )
        w.after_refusal = None

    w.http["after_write"], w.after_refusal = withdraw_policy_after_create, restore_original_policy
    result, _ = await run(
        w,
        client,
        temporal_env,
        # Initial ordinary deploy decorators stay unchanged. This policy is
        # introduced only after actual HTTP dispatch/vote/enqueue, before Worker.
        before=lambda: policies.append(approval_policy(w)),
        workflow_type=SameRunRecoveryWorkflow,
    )
    first, second = result["attempts"]
    assert first == {"refused": True}
    assert (
        second["configuration_observed"]
        and second["runtime_observation_reason"] == "WORKLOAD_ROLLOUT_OBSERVED"
    )
    assert second["workload_ready"] and not second["invocation_verified"]
    assert len(namespace_uids) == len(retained_before_restore) == 1
    assert w.native.writes == 1
    for cls in (GCPGKEPreparationJournal, GCPWorkloadIdentityJournal, GCPGKEAppApplyJournal):
        assert await sync_to_async(lambda cls=cls: cls.objects.get().state)() == "OBSERVED"
    assert await sync_to_async(
        GCPIdentityAcknowledgement.objects.filter(acknowledgement_sha256=retained_before_restore[0]).exists
    )()
    bound = await sync_to_async(DeploymentExecutionReceipt.objects.get)(kind="BOUND")
    assert bound.run_id == w.sdk_run_id


@pytest.mark.asyncio
async def test_registered_activity_drains_delayed_real_heartbeat_callback_on_refusal(
    native_world, client, temporal_env
):
    w = native_world
    from threading import current_thread

    from temporalio import activity

    entered, release, returned = Event(), Event(), Event()
    real_heartbeat = activity.heartbeat

    def delayed_empty_heartbeat(*details):
        assert not details
        real_heartbeat(*details)
        if current_thread().name == "native-configuration-heartbeat":
            entered.set()
            assert release.wait(6)
            returned.set()

    w.monkeypatch.setattr(activity, "heartbeat", delayed_empty_heartbeat)
    w.before_ports = lambda: entered.wait(5) or pytest.fail("registered heartbeat must start")

    def unsupported_before_effect():
        type(w.medops_app).objects.filter(pk=w.medops_app.pk).update(
            manifest_raw=w.medops_app.manifest_raw.replace("replicas = 1", "replicas = 0")
        )

    async def release_in_flight_heartbeat(_handle):
        assert await sync_to_async(entered.wait)(5)
        try:
            # Delay return after the real SDK send, longer than the old 2s join.
            await asyncio.sleep(2.2)
            assert not returned.is_set()
            assert any(thread.name == "native-configuration-heartbeat" for thread in active_threads())
        finally:
            release.set()

    result, _ = await run(
        w,
        client,
        temporal_env,
        before=unsupported_before_effect,
        during=release_in_flight_heartbeat,
    )
    assert result == {"refused": True} and returned.is_set()
    assert not w.native.calls and not w.http["requests"]


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["image", "saved_literal", "raw_manifest"])
async def test_render_source_withdrawal_during_actual_sdk_read_refuses_before_gsa_create(
    native_world, client, temporal_env, changed
):
    w = native_world
    from astrolift_lifecycle.models import Deployment

    held, release = Event(), Event()

    def hold_before_create(name):
        if name == "GetServiceAccount" and not w.native.writes:
            held.set()
            assert release.wait(8)

    async def change_retained_render_tuple(_handle):
        assert await sync_to_async(held.wait)(120)
        try:

            def change():
                if changed == "image":
                    Deployment.objects.filter(pk=w.starts[0].deployment_id).update(image_tag="changed-image")
                elif changed == "saved_literal":
                    Deployment.objects.filter(pk=w.starts[0].deployment_id).update(
                        secret_snapshot={"literals": {"PRIVATE_MARKER": "private-manifest-canary"}}
                    )
                else:
                    type(w.medops_app).objects.filter(pk=w.medops_app.pk).update(
                        manifest_raw=w.medops_app.manifest_raw + "\n# changed raw manifest\n"
                    )

            await sync_to_async(change)()
        finally:
            release.set()

    w.native.after = hold_before_create
    result, _ = await run(w, client, temporal_env, during=change_retained_render_tuple)
    assert result == {"refused": True}
    assert not w.native.writes and not w.http["effects"] and not w.iam_wires
    assert await sync_to_async(GCPIdentityAcknowledgement.objects.count)() == 0
    assert await sync_to_async(GCPGKEPreparationJournal.objects.count)() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("withdrawal", ["render", "token"])
async def test_runtime_db_boundary_rechecks_render_and_original_caller_after_journal_read(
    native_world, client, temporal_env, monkeypatch, withdrawal
):
    from astrolift_lifecycle.models import Deployment
    from astrolift_services.gcp_gke_app_apply_journal import AppApplyStore

    w = native_world
    original = AppApplyStore.validate_runtime_handoff
    changed = []

    def withdraw_after_current_read(store, *args, **kwargs):
        original(store, *args, **kwargs)
        if not changed:
            changed.append(len(w.http["requests"]))
            if withdrawal == "render":
                Deployment.objects.filter(pk=w.starts[0].deployment_id).update(image_tag="changed-image")
            else:
                type(w.token).objects.filter(pk=w.token.pk).update(is_revoked=True)

    monkeypatch.setattr(AppApplyStore, "validate_runtime_handoff", withdraw_after_current_read)
    result, history = await run(w, client, temporal_env)
    assert result == {"refused": True}
    assert len(changed) == 1 and len(w.http["requests"]) == changed[0], json.dumps(
        {
            "safeFailureCauses": getattr(w, "failure_causes", []),
            "temporalHistory": _safe_temporal_history_summary(history),
        },
        sort_keys=True,
    )
    assert not any(path.endswith("/pods?limit=257") for _, path, _ in w.http["requests"])
    assert await sync_to_async(lambda: GCPGKEAppApplyJournal.objects.get().state)() == "OBSERVED"
    if withdrawal == "render":
        assert w.failures[-1] == ("NativeConfigurationError", "CONFIGURATION_RENDER_SOURCE_CHANGED")
    else:
        assert w.failures[-1][0] in ("DeploymentExecutionError", "PermissionDenied")
