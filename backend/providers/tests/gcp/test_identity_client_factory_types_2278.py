"""Actual generated clients keep transport pairing and constructor cleanup."""

import google.auth
import pytest
from google.auth.credentials import AnonymousCredentials
from google.cloud.aiplatform_v1beta1 import EndpointServiceClient
from google.cloud.aiplatform_v1beta1.services.endpoint_service.transports.grpc import EndpointServiceGrpcTransport
from google.cloud.iam_admin_v1 import IAMClient
from google.cloud.iam_admin_v1.services.iam.transports.grpc import IAMGrpcTransport
from google.cloud.resourcemanager_v3 import ProjectsClient
from google.cloud.resourcemanager_v3.services.projects.transports.grpc import ProjectsGrpcTransport

from gcp.identity_owned import NativeGCPIdentity

from .test_identity_owned_2278 import CONTEXT, PROJECT, Wire

TRANSPORTS = (ProjectsGrpcTransport, IAMGrpcTransport, EndpointServiceGrpcTransport)


def channels(monkeypatch):
    made = []
    # Build serializer fixtures before injecting a generated-client failure.
    available = [Wire(), Wire(), Wire()]

    def channel(host, **kwargs):
        wire = available[len(made)]
        made.append(wire)
        return wire

    for transport in TRANSPORTS:
        monkeypatch.setattr(transport, "create_channel", channel)
    monkeypatch.setattr(google.auth, "default", lambda **kwargs: (AnonymousCredentials(), PROJECT))
    return made


def test_actual_generated_client_pairing_and_unwrapped_private_channels(monkeypatch):
    made = channels(monkeypatch)
    checks = []
    with NativeGCPIdentity(CONTEXT) as driver:
        clients = driver._native(lambda: checks.append(True))
        assert tuple(type(client) for client in clients) == (ProjectsClient, IAMClient, EndpointServiceClient)
        assert tuple(type(client.transport) for client in clients) == TRANSPORTS
        for client, wire in zip(clients, made, strict=True):
            assert client.transport._logged_channel is wire
            assert client.transport._wrapped_methods
        assert len(checks) == 8  # Before/after ADC, then before/after each private client.
    assert len(made) == 3 and all(wire.closed == 1 for wire in made)


@pytest.mark.parametrize(
    "family,package",
    [(0, "google.cloud.resourcemanager_v3"), (1, "google.cloud.iam_admin_v1"), (2, "google.cloud.aiplatform_v1beta1")],
)
def test_constructor_failure_closes_prior_clients_and_failed_channel(monkeypatch, family, package):
    from importlib import import_module

    made = channels(monkeypatch)
    name = ("ProjectsClient", "IAMClient", "EndpointServiceClient")[family]
    observed = []

    def fail(*, transport):
        observed.append(type(transport))
        raise RuntimeError("OWNED_CLIENT_CONSTRUCTION_FAILURE")

    monkeypatch.setattr(import_module(package), name, fail)
    with NativeGCPIdentity(CONTEXT) as driver:
        with pytest.raises(RuntimeError, match=r"^OWNED_CLIENT_CONSTRUCTION_FAILURE$"):
            driver._native(lambda: None)
        assert driver._clients is None
    assert observed == [TRANSPORTS[family]]
    assert len(made) == family + 1 and all(wire.closed == 1 for wire in made)
