"""Private operations retain retry exceptions without exporting their contents."""

import asyncio
import inspect
import logging
import sys
from types import SimpleNamespace

import boto3
import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from _sdk import _telemetry
from _sdk._telemetry import driver_op
from aws.identity_irsa import IRSAConfig, IRSADriver
from azure.identity_federated import AzureFederatedIdentityDriver

CANARY = "synthetic-private-identity-2284"


@pytest.mark.parametrize("method", [IRSADriver.reconcile_managed_identity, IRSADriver.verify_managed_identity])
def test_owned_aws_operations_redact_every_argument(method):
    context = method.__astrolift_driver_op__
    parameters = inspect.signature(method).parameters
    assert set(parameters) - {"self"} == set(context.redact_args)
    assert all(value.kind is inspect.Parameter.KEYWORD_ONLY for name, value in parameters.items() if name != "self")
    assert context.redact_errors is True
    assert context.audit is (method is IRSADriver.reconcile_managed_identity)


def test_azure_owned_factory_has_no_effect_heartbeat_or_audit():
    method = AzureFederatedIdentityDriver.owned_reconciler
    context = method.__astrolift_driver_op__
    assert set(inspect.signature(method).parameters) == {"self"}
    assert context.redact_errors is True
    assert context.audit is False
    assert context.heartbeat is False


@pytest.mark.parametrize("method", ["reconcile_managed_identity", "verify_managed_identity"])
def test_actual_iam_denial_contents_do_not_escape_operation_telemetry(method, telemetry, caplog):
    iam = boto3.client(
        "iam", region_name="us-east-1", aws_access_key_id="synthetic-key", aws_secret_access_key="synthetic-secret"
    )
    driver = IRSADriver(
        config=IRSAConfig(
            region="us-east-1",
            account_id="123456789012",
            cluster_oidc_issuer="oidc.eks.us-east-1.amazonaws.com/id/ABC123",
        ),
        iam_client=iam,
    )
    with Stubber(iam) as stub:
        stub.add_client_error("get_role", service_error_code="AccessDenied", service_message=CANARY)
        with caplog.at_level(logging.INFO, logger="astrolift.providers"), pytest.raises(ClientError) as caught:
            getattr(driver, method)(
                name=CANARY,
                permissions=[],
                owner={
                    "organization": "11111111-1111-4111-8111-111111111111",
                    "app": "22222222-2222-4222-8222-222222222222",
                    "cluster": "33333333-3333-4333-8333-333333333333",
                },
                subjects=["system:serviceaccount:synthetic:synthetic"],
            )
        stub.assert_no_pending_responses()
    assert caught.value.response["Error"]["Message"] == CANARY
    exporter, audit = telemetry
    span = exporter.get_finished_spans()[0]
    assert span.status.status_code is StatusCode.ERROR
    assert not span.events
    assert CANARY not in repr(dict(span.attributes))
    assert CANARY not in repr(audit)
    assert CANARY not in caplog.text
    assert all(record.exc_info is None and CANARY not in repr(record.__dict__) for record in caplog.records)


@pytest.fixture
def telemetry(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(_telemetry, "_tracer", provider.get_tracer("private-operation-test"))
    monkeypatch.setattr(_telemetry, "_OTEL_AVAILABLE", True)
    audit = []
    monkeypatch.setattr(_telemetry, "_audit_emit", lambda **entry: audit.append(entry))
    loggers = [logging.getLogger("astrolift.providers"), logging.getLogger("astrolift")]
    previous = [logger.propagate for logger in loggers]
    for logger in loggers:
        logger.propagate = True
    yield exporter, audit
    for logger, value in zip(loggers, previous, strict=True):
        logger.propagate = value
    provider.shutdown()


@pytest.mark.parametrize("shape", ["sync", "async", "stream"])
def test_private_error_hides_messages_args_and_tracebacks_but_preserves_exception(shape, telemetry, caplog):
    error = RuntimeError(CANARY)

    class Driver:
        @driver_op(driver="private", redact_args=("payload", "checkpoint"), redact_errors=True, audit=True)
        def sync(self, **kwargs):
            raise error

        @driver_op(driver="private", redact_args=("payload", "checkpoint"), redact_errors=True, audit=True)
        async def asynchronous(self, **kwargs):
            raise error

        @driver_op(driver="private", redact_args=("payload", "checkpoint"), redact_errors=True, audit=True)
        async def stream(self, **kwargs):
            yield "safe"
            raise error

    async def collect():
        async for _ in Driver().stream(payload=CANARY, checkpoint=CANARY):
            pass

    with caplog.at_level(logging.INFO, logger="astrolift.providers"), pytest.raises(RuntimeError) as caught:
        if shape == "sync":
            Driver().sync(payload=CANARY, checkpoint=CANARY)
        elif shape == "async":
            asyncio.run(Driver().asynchronous(payload=CANARY, checkpoint=CANARY))
        else:
            asyncio.run(collect())
    assert caught.value is error
    exporter, audit = telemetry
    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].status.status_code is StatusCode.ERROR
    assert spans[0].status.description == "Private driver operation failed."
    assert not spans[0].events
    assert CANARY not in repr(dict(spans[0].attributes))
    assert len(audit) == 1
    assert audit[0]["error_code"] == "RuntimeError"
    assert audit[0]["error_message"] == "Private driver operation failed."
    assert CANARY not in repr(audit)
    assert caplog.records
    for record in caplog.records:
        assert record.exc_info is None
        assert CANARY not in repr(record.__dict__)
        assert record.driver_args == {"payload": "<redacted>", "checkpoint": "<redacted>"}
    assert CANARY not in caplog.text


def test_default_error_behavior_is_unchanged(telemetry, caplog):
    error = RuntimeError(CANARY)

    class Driver:
        @driver_op(driver="default", audit=True)
        def call(self, *, payload):
            raise error

    with caplog.at_level(logging.INFO, logger="astrolift.providers"), pytest.raises(RuntimeError) as caught:
        Driver().call(payload=CANARY)
    assert caught.value is error
    exporter, audit = telemetry
    span = exporter.get_finished_spans()[0]
    assert span.status.status_code is StatusCode.ERROR
    assert CANARY in span.status.description
    assert len(span.events) == 1
    assert CANARY in repr(dict(span.events[0].attributes))
    assert audit[0]["error_message"] == CANARY
    assert audit[0]["extra"]["driver_args"] == {"payload": CANARY}
    assert CANARY in caplog.text
    assert any(record.exc_info and record.exc_info[1] is error for record in caplog.records)


@pytest.mark.parametrize("private", [True, False])
def test_failed_audit_writer_cannot_chain_private_native_error(private, monkeypatch, caplog):
    error = RuntimeError(CANARY)

    class Entry:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    def failing_writer(entry):
        raise RuntimeError("synthetic-audit-writer-marker")

    monkeypatch.setitem(sys.modules, "core.mutations", SimpleNamespace(AuditEntry=Entry, emit_audit=failing_writer))
    monkeypatch.setitem(sys.modules, "core.tenancy", SimpleNamespace(get_current_tenant=lambda: None))
    logger = logging.getLogger("astrolift.providers")
    monkeypatch.setattr(logger, "propagate", True)
    monkeypatch.setattr(logging.getLogger("astrolift"), "propagate", True)

    class Driver:
        @driver_op(driver="audit-control", audit=True, redact_errors=private)
        def call(self):
            raise error

    with caplog.at_level(logging.WARNING, logger="astrolift.providers"), pytest.raises(RuntimeError) as caught:
        Driver().call()
    assert caught.value is error
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    if private:
        assert all(not record.exc_info for record in caplog.records)
        assert CANARY not in caplog.text
        assert "synthetic-audit-writer-marker" not in caplog.text
    else:
        assert warnings[0].exc_info
        assert CANARY in caplog.text
        assert "synthetic-audit-writer-marker" in caplog.text
