"""Real Cognitive Services SDK requests/decoding with controlled ARM transport."""

from __future__ import annotations

import copy
import json
import time
from dataclasses import asdict
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from azure.core.credentials import AccessToken
from azure.core.pipeline.transport import HttpResponse, HttpTransport
from azure.foundry_catalogue import (
    CatalogueState,
    FoundryCatalogue,
    FoundryCatalogueConfig,
    FoundryCatalogueError,
)

SUBSCRIPTION = "018f42f0-4420-7000-8000-000000000002"
CONFIG = FoundryCatalogueConfig(SUBSCRIPTION, "controlled-rg", "controlled-foundry", "eastus2")
ACCOUNT = CONFIG.account_id
COLLECTION = ACCOUNT + "/deployments"


class Credential:
    def __init__(self):
        self.scopes: list[tuple[str, ...]] = []

    def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken:
        self.scopes.append(scopes)
        return AccessToken("controlled-offline-only", int(time.time()) + 3600)


class Response(HttpResponse):
    def __init__(self, request, code: int, body: dict, headers: dict | None = None):
        super().__init__(request, None)
        self.status_code = code
        self.headers = {"Content-Type": "application/json", **(headers or {})}
        self.content_type = "application/json"
        self.reason = "controlled response"
        self._body = json.dumps(body).encode()

    def body(self):
        return self._body

    def read(self):
        return self._body

    def iter_bytes(self):
        yield self._body

    def iter_raw(self):
        yield self._body

    def json(self):
        return json.loads(self._body)

    def stream_download(self, *args, **kwargs):
        return iter([self._body])


def deployment(name: str = "model-a") -> dict:
    return {
        "id": COLLECTION + "/" + name,
        "name": name,
        "type": "Microsoft.CognitiveServices/accounts/deployments",
        "sku": {"name": "GlobalStandard", "capacity": 10},
        "properties": {
            "model": {"format": "Meta", "name": "controlled-model", "version": "1"},
            "provisioningState": "Succeeded",
        },
    }


class ArmTransport(HttpTransport):
    def __init__(self):
        self.account = {
            "id": ACCOUNT,
            "name": CONFIG.account_name,
            "type": "Microsoft.CognitiveServices/accounts",
            "kind": "AIServices",
            "location": CONFIG.region,
            "properties": {"disableLocalAuth": True},
        }
        self.accounts: list[dict] = []
        self.pages: list[dict] = [{"value": [deployment()]}]
        self.detail = deployment()
        self.failures: dict[str, int] = {}
        self.redirect: str | None = None
        self.calls: list[tuple[str, str, dict]] = []
        self.closed = False

    def open(self):
        pass

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def send(self, request, **kwargs):
        url = urlsplit(request.url)
        path = unquote(url.path)
        self.calls.append((request.method, path, kwargs))
        assert request.method == "GET"
        assert url.netloc == "management.azure.com"
        assert kwargs["connection_timeout"] == 5
        assert kwargs["read_timeout"] == 10
        kind = "account" if path == ACCOUNT else "list" if path == COLLECTION else "detail"
        if kind in self.failures:
            return Response(
                request,
                self.failures[kind],
                {"error": {"code": "controlled", "message": "CALLBACK-PHI-NATIVE-MARKER secret-key"}},
            )
        if kind == "account":
            return Response(request, 200, self.accounts.pop(0) if self.accounts else copy.deepcopy(self.account))
        if self.redirect:
            return Response(request, 302, {}, {"Location": self.redirect})
        if kind == "detail":
            return Response(request, 200, copy.deepcopy(self.detail))
        index = int(parse_qs(url.query).get("$skip", ["0"])[0])
        return Response(request, 200, copy.deepcopy(self.pages[index]))


@pytest.fixture
def native():
    transport = ArmTransport()
    credential = Credential()
    catalogue = FoundryCatalogue(CONFIG, credential, transport=transport)
    yield catalogue, transport, credential
    catalogue.close()
    assert transport.closed


def test_native_sdk_decodes_exact_foundry_identity_without_access_claim(native):
    catalogue, transport, credential = native
    result = catalogue.deployments()
    assert result.state == CatalogueState.COMPLETE
    assert result.identity.resource_id == ACCOUNT
    assert result.identity.local_auth_disabled is True
    source = result.sources[0]
    assert (source.resource_id, source.model_format, source.model_name, source.model_version) == (
        COLLECTION + "/model-a",
        "Meta",
        "controlled-model",
        "1",
    )
    assert (source.sku_name, source.declared_capacity, source.provisioning_state) == ("GlobalStandard", 10, "Succeeded")
    assert source.inference_access == "unknown"
    assert [p for _, p, _ in transport.calls] == [ACCOUNT, COLLECTION, ACCOUNT]
    assert credential.scopes == [("https://management.azure.com/.default",)]


def test_actual_sdk_paging_and_detail(native):
    catalogue, transport, _ = native
    transport.pages = [
        {"value": [deployment()], "nextLink": "https://management.azure.com" + COLLECTION + "?$skip=1"},
        {"value": [deployment("model-b")]},
    ]
    page = catalogue.deployments()
    assert page.state == CatalogueState.COMPLETE and page.pages_read == 2
    assert [r.deployment_name for r in page.sources] == ["model-a", "model-b"]
    detail = catalogue.deployment("model-a")
    assert detail.state == CatalogueState.COMPLETE and detail.sources == (page.sources[0],)


@pytest.mark.parametrize("limit,expected,truncated", [(1, 1, True), (2, 2, False), (3, 2, False)])
def test_same_page_limit_distinguishes_complete_from_truncated(native, limit, expected, truncated):
    catalogue, transport, _ = native
    transport.pages = [{"value": [deployment(), deployment("model-b")]}]
    result = catalogue.deployments(limit=limit)
    assert len(result.sources) == expected
    assert (result.state == CatalogueState.TRUNCATED) is truncated


def test_page_cap_never_requests_an_extra_page(native):
    catalogue, transport, _ = native
    transport.pages = [{"value": [deployment()], "nextLink": "https://management.azure.com" + COLLECTION + "?$skip=1"}]
    result = catalogue.deployments(max_pages=1)
    assert result.state == CatalogueState.TRUNCATED and result.pages_read == 1
    assert len(transport.calls) == 3


@pytest.mark.parametrize(
    "next_link",
    [
        "https://evil.example/steal",
        "http://management.azure.com" + COLLECTION,
        "https://management.azure.com" + COLLECTION.replace(SUBSCRIPTION, "foreign-subscription"),
        "https://management.azure.com" + ACCOUNT + "/listKeys",
        "https://management.azure.com" + COLLECTION + "/../listKeys",
    ],
)
def test_server_continuation_cannot_leave_reviewed_read_path(native, next_link):
    catalogue, transport, _ = native
    transport.pages = [{"value": [deployment()], "nextLink": next_link}]
    result = catalogue.deployments()
    # Azure's bearer policy rejects HTTP before our transport is entered.
    assert result.state == (CatalogueState.ERROR if next_link.startswith("http:") else CatalogueState.INVALID_IDENTITY)
    assert result.identity is None and not result.sources
    assert len(transport.calls) == 2


@pytest.mark.parametrize("redirect", ["https://evil.example/read", "https://management.azure.com/other"])
def test_redirect_is_guarded_before_transport_or_token_can_reach_destination(native, redirect):
    catalogue, transport, _ = native
    transport.redirect = redirect
    result = catalogue.deployments()
    # The SDK may refuse a redirect itself; either way no target request is sent.
    assert result.state in (CatalogueState.ERROR, CatalogueState.INVALID_IDENTITY)
    assert len(transport.calls) == 2 and not result.sources


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", ACCOUNT.replace(SUBSCRIPTION, "other")),
        ("name", "other-account"),
        ("location", "westus"),
        ("kind", "OpenAI"),
        ("type", "Microsoft.Other/accounts"),
    ],
)
def test_foreign_or_unsupported_account_blocks_all_model_reads(native, field, value):
    catalogue, transport, _ = native
    transport.account[field] = value
    result = catalogue.deployments()
    assert result.state == CatalogueState.INVALID_IDENTITY and not result.sources
    assert len(transport.calls) == 1


def test_fresh_account_recheck_refuses_retargeted_authentication(native):
    catalogue, transport, _ = native
    changed = copy.deepcopy(transport.account)
    changed["properties"]["disableLocalAuth"] = False
    transport.accounts = [copy.deepcopy(transport.account), changed]
    result = catalogue.deployments()
    assert result.state == CatalogueState.INVALID_IDENTITY and result.identity is None and not result.sources


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", COLLECTION.replace(SUBSCRIPTION, "other") + "/model-a"),
        ("name", "foreign"),
        ("type", "Microsoft.Other/accounts/deployments"),
    ],
)
def test_exact_detail_target_cannot_be_replaced(native, field, value):
    catalogue, transport, _ = native
    transport.detail[field] = value
    result = catalogue.deployment("model-a")
    assert result.state == CatalogueState.INVALID_IDENTITY and not result.sources


def test_repeated_native_resource_is_not_a_complete_inventory(native):
    catalogue, transport, _ = native
    transport.pages = [{"value": [deployment(), deployment()]}]
    assert catalogue.deployments().state == CatalogueState.INVALID_IDENTITY


@pytest.mark.parametrize(
    "code,state",
    [
        (400, CatalogueState.ERROR),
        (401, CatalogueState.DENIED),
        (403, CatalogueState.DENIED),
        (404, CatalogueState.NOT_FOUND),
        (429, CatalogueState.ERROR),
        (503, CatalogueState.ERROR),
    ],
)
def test_native_errors_do_not_become_empty_success_or_leak_diagnostics(native, caplog, code, state):
    catalogue, transport, _ = native
    transport.failures["list"] = code
    result = catalogue.deployments()
    assert result.state == state and result.identity is None and not result.sources
    assert len(transport.calls) == 2
    assert "CALLBACK-PHI-NATIVE-MARKER" not in str(asdict(result)) + caplog.text
    assert "secret-key" not in str(asdict(result)) + caplog.text


@pytest.mark.parametrize("name", ["", "../listKeys", "model/a", "https://evil.example", "x" * 65, "a%2Fb"])
def test_invalid_detail_target_makes_no_requests(native, name):
    catalogue, transport, _ = native
    with pytest.raises(FoundryCatalogueError):
        catalogue.deployment(name)
    assert not transport.calls


@pytest.mark.parametrize("limit,pages", [(0, 1), (501, 1), (True, 1), (1, 0), (1, 6), (1, True)])
def test_invalid_bounds_make_no_requests(native, limit, pages):
    catalogue, transport, _ = native
    with pytest.raises(FoundryCatalogueError):
        catalogue.deployments(limit=limit, max_pages=pages)
    assert not transport.calls


def test_missing_optional_metadata_stays_unknown(native):
    catalogue, transport, _ = native
    transport.account["properties"] = {}
    transport.pages[0]["value"][0].pop("sku")
    transport.pages[0]["value"][0]["properties"]["model"].pop("version")
    transport.pages[0]["value"][0]["properties"].pop("provisioningState")
    result = catalogue.deployments()
    assert result.state == CatalogueState.COMPLETE and result.identity.local_auth_disabled is None
    source = result.sources[0]
    assert source.model_version is None and source.declared_capacity is None and source.provisioning_state is None
    assert source.inference_access == "unknown"


def test_transport_refuses_write_even_if_sdk_client_is_misused(native):
    catalogue, transport, _ = native
    with pytest.raises(FoundryCatalogueError):
        catalogue._client.deployments.begin_delete(CONFIG.resource_group, CONFIG.account_name, "model-a")
    assert not transport.calls
