"""Actual SDK transport proves bounded requests without cloud or LRO polling."""

from urllib.parse import parse_qs, urlsplit

import pytest

from azure.core.pipeline.transport import HttpTransport
from azure.managed.event_grid_namespace_observation import Observation, bounded
from azure.managed.event_grid_namespace_ownership import OwnershipUnknown
from azure.mgmt.eventgrid import EventGridManagementClient, models
from tests.azure.test_event_grid_namespace_receipts_2032 import RG, SUB, target
from tests.azure.test_event_grid_wire_2032 import _Credential, _Response


class Transport(HttpTransport):
    def __init__(self, pages=None, *, pending=False):
        self.responses = pages or [{"value": []}]
        self.pending = pending
        self.calls = []

    def open(self):
        pass

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def send(self, request, **kwargs):
        self.calls.append((request.method, request.url, kwargs))
        assert 0 < kwargs["connection_timeout"] <= 5
        assert 0 < kwargs["read_timeout"] <= 5
        parsed = urlsplit(request.url)
        assert parsed.hostname == "management.azure.com"
        if request.method == "GET":
            index = int(parse_qs(parsed.query).get("$skip", ["0"])[0])
            return _Response(request, 200, self.responses[index])
        assert request.method == "PUT"
        response = _Response(
            request,
            201 if self.pending else 200,
            {
                "id": parsed.path,
                "name": parsed.path.rsplit("/", 1)[1],
                "properties": {"provisioningState": "Creating" if self.pending else "Succeeded"},
            },
        )
        if self.pending:
            response.headers["Azure-AsyncOperation"] = "https://management.azure.com/never-poll-this"
        return response


def fixture(pages=None, **kwargs):
    transport = Transport(pages, **kwargs)
    return Observation(), EventGridManagementClient(_Credential(), SUB, transport=transport), transport


def collection():
    return target().namespace_id + "/topics"


@bounded
def read_pages(observer, client):
    return observer.pages(client.namespace_topics.list_by_namespace, collection(), RG, target().namespace)


def test_actual_complete_empty_inventory():
    observer, client, transport = fixture()
    assert read_pages(observer, client) == []
    assert len(transport.calls) == 1


@pytest.mark.parametrize("pending", [False, True])
def test_sdk_acceptance_never_polls_even_with_async_operation_header(pending):
    observer, client, transport = fixture(pending=pending)

    @bounded
    def create():
        return observer.begin(
            client.namespace_topics.begin_create_or_update,
            RG,
            target().namespace,
            target().topic,
            models.NamespaceTopic(
                publisher_type="Custom", input_schema="CloudEventSchemaV1_0", event_retention_in_days=1
            ),
        )

    result = create()
    assert result.provisioning_state == ("Creating" if pending else "Succeeded")
    assert [method for method, *_ in transport.calls] == ["PUT"]


@pytest.mark.parametrize(
    "next_link",
    [
        "http://management.azure.com",
        "https://evil.example/topics",
        "https://management.azure.com:444" + collection(),
        "https://user@management.azure.com" + collection(),
        "https://management.azure.com" + collection() + "#fragment",
        "https://management.azure.com" + collection().replace(RG, "foreign-rg"),
        "https://management.azure.com" + collection() + "/other",
        "https://management.azure.com:bad" + collection(),
    ],
)
def test_untrusted_continuation_refuses_before_second_request(next_link):
    observer, client, transport = fixture([{"value": [], "nextLink": next_link}])
    with pytest.raises(OwnershipUnknown):
        read_pages(observer, client)
    assert len(transport.calls) == 1


def test_actual_same_collection_continuation_is_consumed():
    following = "https://management.azure.com" + collection() + "?api-version=2025-02-15&$skip=1"
    observer, client, transport = fixture([{"value": [], "nextLink": following}, {"value": []}])
    assert read_pages(observer, client) == []
    assert len(transport.calls) == 2


@pytest.mark.parametrize("count", [128, 129])
def test_inventory_item_limit(count):
    observer, client, _ = fixture([{"value": [{"name": f"item{i}"} for i in range(count)]}])
    if count == 128:
        assert len(read_pages(observer, client)) == 128
    else:
        with pytest.raises(OwnershipUnknown, match="128-item"):
            read_pages(observer, client)


def test_page_limit_refuses_before_fifth_request():
    payloads = [
        {
            "value": [],
            "nextLink": "https://management.azure.com" + collection() + f"?api-version=2025-02-15&$skip={i + 1}",
        }
        for i in range(5)
    ]
    observer, client, transport = fixture(payloads)
    with pytest.raises(OwnershipUnknown, match="four-page"):
        read_pages(observer, client)
    assert len(transport.calls) == 4


def test_large_response_refuses_complete_inventory_claim():
    observer, client, transport = fixture([{"value": [], "padding": "x" * (2 * 1024 * 1024)}])
    with pytest.raises(OwnershipUnknown, match="response exceeds"):
        read_pages(observer, client)
    assert len(transport.calls) == 1


def test_requests_outside_operation_refuse_and_context_is_restored():
    observer, client, transport = fixture()
    with pytest.raises(OwnershipUnknown, match="budget"):
        observer.call(client.namespaces.get, RG, target().namespace)
    assert not transport.calls
    assert read_pages(observer, client) == []
    with pytest.raises(OwnershipUnknown, match="budget"):
        observer.call(client.namespaces.get, RG, target().namespace)
    assert len(transport.calls) == 1
