"""Confidential driver instrumentation preserves errors without publishing task inputs."""

import logging
from types import SimpleNamespace

import pytest

from _sdk import _telemetry
from aws.cluster_eks import EKSClusterDriver


@pytest.mark.parametrize(
    "method", ["prepare_confidential_task", "cleanup_confidential_task", "apply_manifests", "probe_nitro_readiness"]
)
def test_confidential_driver_telemetry_redacts_inputs_and_error_details(method, monkeypatch, caplog):
    private = "private-task-payload-and-error"
    error = ValueError(private)
    audit = []
    monkeypatch.setattr(_telemetry, "_audit_emit", lambda **values: audit.append(values))
    monkeypatch.setattr(logging.getLogger("astrolift.providers"), "propagate", True)
    monkeypatch.setattr(logging.getLogger("astrolift"), "propagate", True)

    def fail(*args, **kwargs):
        raise error

    driver = object.__new__(EKSClusterDriver)
    driver._config = SimpleNamespace(region="us-west-2", credential=None)
    driver.validate_confidential_task = fail
    driver._k8s = fail
    monkeypatch.setattr("aws.cluster_eks.aws_session", fail)
    monkeypatch.setattr("aws.nitro_readiness.collect_readiness", fail)
    kwargs = {"cluster": "cluster", "namespace": "agents"}
    if method == "prepare_confidential_task":
        kwargs.update(
            config=private,
            pod={"env": private},
            secret={"value": private},
            task_id="task",
            callback_hash=private,
            save=private,
        )
    elif method == "cleanup_confidential_task":
        kwargs = {"checkpoint": {"value": private}}
    elif method == "apply_manifests":
        kwargs["manifests"] = [{"kind": "Secret", "stringData": {"token": private}}]
    else:
        kwargs["config"] = private

    with caplog.at_level(logging.INFO, logger="astrolift.providers"), pytest.raises(ValueError) as raised:
        getattr(driver, method)(**kwargs)
    assert raised.value is error
    records = [r for r in caplog.records if getattr(r, "method", None) == method]
    assert records
    assert private not in repr([r.__dict__ for r in records])
    assert private not in repr(audit)
    assert any("<redacted>" in repr(r.driver_args) for r in records)
    assert all(r.exc_info is None for r in records)
    if method in {"prepare_confidential_task", "cleanup_confidential_task"}:
        assert audit and audit[0]["decision"] == "ERROR"
