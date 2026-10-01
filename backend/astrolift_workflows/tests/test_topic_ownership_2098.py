"""Real persisted lifecycle rows retain source identity at the Pub/Sub boundary."""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from unittest.mock import patch

import pytest
from gcp.managed.topic_pubsub import PubSubTopicConfig, PubSubTopicDriver, PubSubTopicError

from astrolift_workflows.activities.managed_service_lifecycle import (
    _deprovision_sync,
    _managed_binding_for,
    _provision_sync,
    _update_sync,
    build_provision_spec,
)
from astrolift_workflows.tests.test_recorded_handle_exclusive_2086 import _service

pytestmark = pytest.mark.django_db


class NotFound(Exception):
    pass


class TopicAPI:
    def __init__(self) -> None:
        self.topics: dict[str, Any] = {}
        self.writes: list[tuple[str, Any]] = []

    @staticmethod
    def topic_path(project: str, topic: str) -> str:
        return f"projects/{project}/topics/{topic}"

    def get_topic(self, *, request: dict[str, Any]) -> Any:
        if request["topic"] not in self.topics:
            raise NotFound(request["topic"])
        return deepcopy(self.topics[request["topic"]])

    def create_topic(self, *, request: dict[str, Any]) -> Any:
        assert request["name"] not in self.topics
        self.writes.append(("create", deepcopy(request)))
        self.topics[request["name"]] = deepcopy(request)
        return request

    def update_topic(self, *, request: dict[str, Any]) -> Any:
        self.writes.append(("update", deepcopy(request)))
        topic = self.topics[request["topic"]["name"]]
        for field in request["update_mask"]["paths"]:
            topic[field] = request["topic"][field]
        return deepcopy(topic)

    def list_topic_subscriptions(self, *, request: dict[str, Any]) -> list[Any]:
        return []

    def delete_topic(self, *, request: dict[str, Any]) -> None:
        self.writes.append(("delete", deepcopy(request)))
        del self.topics[request["topic"]]


@pytest.fixture
def world():
    svc = _service(org_slug="pubsub-2098", variant="pubsub", backend_ref="")
    svc.kind = "topic"
    svc.save(update_fields=["kind"])
    api = TopicAPI()
    cfg = PubSubTopicConfig(project_id="shared-project", publisher_client=api, subscriber_client=api)
    with (
        patch("astrolift_drivers.registry.plugins.get", return_value=PubSubTopicDriver),
        patch("core.cluster_observability.managed_config_for", return_value=cfg),
    ):
        yield svc, api, PubSubTopicDriver(config=cfg)


def test_actual_lifecycle_keeps_identity_through_update_binding_and_teardown(world) -> None:
    svc, api, _ = world
    result = _provision_sync(svc.pk)
    assert result["ok"]
    svc.backend_ref = result["handle"]
    svc.config = {"labels": {"department": "new"}}
    svc.save(update_fields=["backend_ref", "config"])
    assert _update_sync(svc.pk)["ok"]
    assert next(iter(api.topics.values()))["labels"]["astrolift_io_managed_service_id"] == str(svc.guid)
    assert _managed_binding_for(svc).env_vars["PUBSUB_TOPIC"].literal == next(iter(api.topics))
    assert _deprovision_sync(svc.pk, True, True)["ok"]
    assert api.topics == {}


def test_foreign_live_provider_identity_refuses_all_lifecycle_writes(world) -> None:
    svc, api, driver = world
    topic_id = driver._topic_id(build_provision_spec(svc, cluster=svc.app_environment.tenant_cluster))
    topic_path = api.topic_path("shared-project", topic_id)
    api.topics[topic_path] = {"name": topic_path, "labels": {"astrolift_io_managed_service_id": "foreign"}}
    svc.backend_ref = f"topic/{topic_id}"
    svc.config = {"labels": {"department": "new"}}
    svc.save(update_fields=["backend_ref", "config"])
    assert not _provision_sync(svc.pk)["ok"]
    assert not _update_sync(svc.pk)["ok"]
    assert not _deprovision_sync(svc.pk, True, True)["ok"]
    with pytest.raises(PubSubTopicError, match="another managed service"):
        _managed_binding_for(svc)
    assert api.writes == []
    assert api.topics[topic_path]["labels"] == {"astrolift_io_managed_service_id": "foreign"}
