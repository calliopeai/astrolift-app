"""A Cloud Operations binding reads and writes only its own bundle's logs (#2099).

The binding granted ``roles/logging.viewer``, ``logging.logWriter``,
``monitoring.viewer`` and ``monitoring.metricWriter`` on the whole project, so in
a project several orgs share a bound workload read every tenant's logs and
metrics. Reads are now ``roles/logging.viewAccessor`` on the bundle bucket's
view. Writes need no role: the platform stamps the bundle's pod label on the
bound workload's pods, and the bundle routes those pods' GKE container entries
into its bucket and keeps them out of ``_Default``. Metrics are read through
Astrolift's own API, so no Cloud Monitoring role is granted. The workload
identity withdraws the project-wide roles an earlier binding left behind, and
takes a detached bundle's view grant back.

``LogRouter`` replays the project's Log Router over the fake client's sinks and
exclusions, against entries shaped the way GKE's logging agent writes them.
"""

from __future__ import annotations

import re
from dataclasses import replace
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from _sdk.managed_service import DeprovisionSpec, ServiceHandle, UpdateSpec
from gcp._errors import ProviderError
from gcp.identity_wi import GCPWIConfig, GCPWorkloadIdentityDriver
from gcp.managed.observability_cloud_operations import (
    BUNDLE_POD_LABEL,
    CloudOperationsError,
    bundle_pod_labels,
)

from .test_identity_wi import FakeIAMClient, FakeProjectIAMClient, _AlreadyExists
from .test_managed_observability_cloud_operations import (
    MSID,
    FakeCloudOperationsClient,
    _driver,
    _spec,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

_PROJECT_WIDE = {
    "roles/logging.viewer",
    "roles/logging.logWriter",
    "roles/monitoring.viewer",
    "roles/monitoring.metricWriter",
    "roles/logging.viewAccessor",
}
_SA = "serviceAccount:app-identity@acme-prod.iam.gserviceaccount.com"
_ENTRY_LABEL = f'labels."k8s-pod/{BUNDLE_POD_LABEL}"'


# ----- a Log Router over the fake client ---------------------------------

_TOKEN = re.compile(r'\s*(?:(\()|(\))|(:\*)|(=)|(\.)|"((?:[^"\\]|\\.)*)"|([A-Za-z_][A-Za-z0-9_-]*))')


def _tokens(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    text = text.strip()
    position = 0
    while position < len(text):
        match = _TOKEN.match(text, position)
        if match is None:
            raise ValueError(f"the fake Log Router does not read {text[position:]!r}")
        position = match.end()
        opening, closing, present, equals, dot, quoted, word = match.groups()
        if word in {"AND", "OR", "NOT"}:
            tokens.append((word, word))
        elif word is not None:
            tokens.append(("word", word))
        elif quoted is not None:
            tokens.append(("quoted", quoted))
        else:
            symbol = opening or closing or present or equals or dot
            tokens.append((symbol, symbol))
    return tokens


class _Query:
    """The Logging query language subset the bundle writes, with its precedence:
    NOT binds tightest, then OR, then AND."""

    def __init__(self, text: str) -> None:
        self._tokens = _tokens(text)
        self._at = 0
        self.matches = self._conjunction() if self._tokens else (lambda entry: True)
        if self._at != len(self._tokens):
            raise ValueError(f"the fake Log Router does not read {text!r}")

    def _peek(self) -> str | None:
        return self._tokens[self._at][0] if self._at < len(self._tokens) else None

    def _take(self, *kinds: str) -> str:
        kind = self._peek()
        if kind not in kinds:
            raise ValueError(f"expected {kinds}, found {kind!r}")
        self._at += 1
        return self._tokens[self._at - 1][1]

    def _conjunction(self) -> Callable[[Mapping[str, Any]], bool]:
        terms = [self._disjunction()]
        while self._peek() == "AND":
            self._take("AND")
            terms.append(self._disjunction())
        return lambda entry: all(term(entry) for term in terms)

    def _disjunction(self) -> Callable[[Mapping[str, Any]], bool]:
        terms = [self._negation()]
        while self._peek() == "OR":
            self._take("OR")
            terms.append(self._negation())
        return lambda entry: any(term(entry) for term in terms)

    def _negation(self) -> Callable[[Mapping[str, Any]], bool]:
        if self._peek() == "NOT":
            self._take("NOT")
            inner = self._negation()
            return lambda entry: not inner(entry)
        if self._peek() == "(":
            self._take("(")
            inner = self._conjunction()
            self._take(")")
            return inner
        path = [self._take("word", "quoted")]
        while self._peek() == ".":
            self._take(".")
            path.append(self._take("word", "quoted"))
        if self._peek() == ":*":
            self._take(":*")
            return lambda entry: _field(entry, path) is not None
        self._take("=")
        value = self._take("word", "quoted")
        return lambda entry: _field(entry, path) == value


def _field(entry: Mapping[str, Any], path: list[str]) -> Any:
    current: Any = entry
    for part in path:
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def gke_container_entry(pod_labels: Mapping[str, str], *, namespace: str = "checkout") -> dict[str, Any]:
    """A container log entry as GKE's logging agent writes one for a pod carrying ``pod_labels``.

    Pod labels land on the entry's ``labels`` as ``k8s-pod/<key>``, each ``.`` in
    the key folded to ``_``; ``resource.labels`` say only where the container runs.
    """
    return {
        "resource": {
            "type": "k8s_container",
            "labels": {
                "project_id": "acme-prod",
                "location": "us-central1",
                "cluster_name": "shared",
                "namespace_name": namespace,
                "pod_name": "web-7d9f",
                "container_name": "web",
            },
        },
        "labels": {f"k8s-pod/{key.replace('.', '_')}": value for key, value in pod_labels.items()},
        "textPayload": "GET / 200",
    }


class LogRouter:
    """Where the project's Log Router delivers an entry: every enabled sink whose
    filter matches it, and ``_Default`` unless an enabled exclusion matches it."""

    def __init__(self, logging: FakeCloudOperationsClient) -> None:
        self._logging = logging

    def destinations(self, entry: Mapping[str, Any]) -> set[str]:
        reached = {
            str(sink["destination"])
            for sink in self._logging.resources.get("log_sinks", {}).values()
            if not sink.get("disabled") and _Query(str(sink.get("filter") or "")).matches(entry)
        }
        exclusions = self._logging.resources.get("log_exclusions", {}).values()
        if not any(not item.get("disabled") and _Query(str(item["filter"])).matches(entry) for item in exclusions):
            reached.add("_Default")
        return reached


# ----- fixtures ------------------------------------------------------------


class _HTTPError(Exception):
    """What the REST adapter's ``raise_for_status`` raises."""

    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.response = SimpleNamespace(status_code=status)


class FakeIAMWithViews(FakeProjectIAMClient):
    def __init__(self, bindings: list[dict[str, Any]] | None = None) -> None:
        super().__init__(policy={"version": 1, "etag": "etag-1", "bindings": bindings or []})
        self.views: dict[str, dict[str, Any]] = {}
        self.view_set_calls: list[str] = []
        self.view_errors: dict[str, int] = {}

    def get_log_view_iam_policy(self, *, view: str) -> dict[str, Any]:
        if view in self.view_errors:
            raise _HTTPError(self.view_errors[view])
        policy = self.views.get(view, {"etag": "view-etag", "bindings": []})
        return {**policy, "bindings": [dict(item) for item in policy["bindings"]]}

    def set_log_view_iam_policy(self, *, view: str, policy: dict[str, Any]) -> dict[str, Any]:
        self.views[view] = policy
        self.view_set_calls.append(view)
        return policy


def _identity(project_iam: FakeIAMWithViews) -> GCPWorkloadIdentityDriver:
    # The driver recognises an existing account by the exception's name.
    _AlreadyExists.__name__ = "AlreadyExists"
    return GCPWorkloadIdentityDriver(
        config=GCPWIConfig(project_id="acme-prod", iam_client=FakeIAMClient(), project_iam_client=project_iam),
    )


def _bound(config: dict[str, Any] | None = None, *, msid: str = MSID, logging: FakeCloudOperationsClient | None = None):
    driver, logging, _ = _driver(logging=logging)
    cfg = {"deletion_protection": False, **(config or {})}
    result = driver.provision(replace(_spec(cfg), managed_service_id=msid))
    assert result.ok, result.message
    handle = ServiceHandle(result.handle, managed_service_id=msid)
    return driver, logging, handle, cfg


def _bucket_destination(driver: Any, handle: ServiceHandle, cfg: dict[str, Any]) -> str:
    return f"logging.googleapis.com/{driver.binding(handle, cfg).env_vars['GCP_LOG_BUCKET'].literal}"


def _gcp_permissions(binding: Any) -> list[dict[str, Any]]:
    """The binding's grants in the shape the identity activity hands the GCP driver."""
    return [{"role": action, "resource": grant.resource} for grant in binding.iam_grants for action in grant.actions]


def _project_roles_for(member: str, policy: dict[str, Any]) -> set[str]:
    return {binding["role"] for binding in policy["bindings"] if member in binding.get("members", [])}


# ----- reads ---------------------------------------------------------------


@pytest.mark.parametrize("mode", ["read", "write", "both", "logs", "metrics", "none"])
def test_no_bundle_binding_grants_anything_on_the_project(mode: str) -> None:
    driver, _, handle, cfg = _bound({"access_mode": mode})

    binding = driver.binding(handle, {**cfg, "access_mode": mode})

    assert not [grant for grant in binding.iam_grants if grant.resource == "projects/acme-prod"]
    assert not {action for grant in binding.iam_grants for action in grant.actions} & (
        _PROJECT_WIDE - {"roles/logging.viewAccessor"}
    )
    bucket = binding.env_vars["GCP_LOG_BUCKET"].literal
    expected = (
        [(f"{bucket}/views/_AllLogs", ["roles/logging.viewAccessor"])] if mode in {"read", "both", "logs"} else []
    )
    assert [(grant.resource, grant.actions) for grant in binding.iam_grants] == expected


def test_two_bundles_in_one_project_read_only_their_own_view() -> None:
    first_driver, _, first, cfg = _bound()
    second_driver, _, second, _ = _bound(msid="managed-2")

    first_views = {grant.resource for grant in first_driver.binding(first, cfg).iam_grants}
    second_views = {grant.resource for grant in second_driver.binding(second, cfg).iam_grants}

    assert len(first_views) == len(second_views) == 1
    assert first_views != second_views


def test_a_bundle_without_a_bucket_is_granted_nothing() -> None:
    driver, logging, handle, cfg = _bound({"log_bucket": False})

    assert driver.binding(handle, cfg).iam_grants == []
    assert not logging.resources.get("log_sinks") and not logging.resources.get("log_exclusions")


def test_binding_notes_name_the_pod_label_and_grant_no_monitoring_role() -> None:
    driver, _, handle, cfg = _bound()

    notes = driver.binding(handle, cfg).notes

    assert f"{BUNDLE_POD_LABEL}=<GCP_CLOUD_OPERATIONS_BUNDLE>" in notes
    assert "no Cloud Monitoring role is granted in any mode" in notes
    assert "Astrolift's scoped observability API" in notes
    assert "no log-write role" in notes


# ----- writes: the platform pod label and the route -----------------------


@pytest.mark.parametrize(
    ("mode", "labelled"),
    [("write", True), ("both", True), ("logs", True), ("read", False), ("metrics", False), ("none", False)],
)
def test_the_pod_label_follows_the_log_write_modes(mode: str, labelled: bool) -> None:
    _, _, handle, cfg = _bound({"access_mode": mode})
    bundle = handle.handle.rsplit("/", 1)[-1]

    assert bundle_pod_labels(handle.handle, cfg) == ({BUNDLE_POD_LABEL: bundle} if labelled else {})


def test_a_bundle_without_a_bucket_labels_no_pod() -> None:
    _, _, handle, cfg = _bound({"log_bucket": {"enabled": False}})

    assert bundle_pod_labels(handle.handle, cfg) == {}


def test_a_malformed_handle_labels_nothing_and_says_so() -> None:
    with pytest.raises(CloudOperationsError, match="must be 'observability/<location>/<bundle-id>'"):
        bundle_pod_labels("observability/global", {})


def test_the_route_selects_the_container_entries_of_pods_carrying_the_bundles_label() -> None:
    _, logging, handle, _ = _bound()
    bundle = handle.handle.rsplit("/", 1)[-1]
    bucket = next(iter(logging.resources["log_bucket"]))
    entries = f'resource.type="k8s_container" AND {_ENTRY_LABEL}="{bundle}"'

    sink = logging.resources["log_sinks"][f"projects/acme-prod/sinks/{bundle}.route"]
    exclusion = logging.resources["log_exclusions"][f"projects/acme-prod/exclusions/{bundle}.route"]

    assert sink["destination"] == f"logging.googleapis.com/{bucket}"
    assert sink["filter"] == exclusion["filter"] == entries
    create = next(call for call in logging.calls if call[0:2] == ("create", "log_sinks"))
    assert create[5] == {"uniqueWriterIdentity": "false"}


def test_a_pod_labelled_for_one_bundle_lands_only_in_that_bundles_bucket() -> None:
    first_driver, logging, first, cfg = _bound(msid="managed-a")
    second_driver, _, second, _ = _bound(msid="managed-b", logging=logging)
    router = LogRouter(logging)
    first_bucket = _bucket_destination(first_driver, first, cfg)
    second_bucket = _bucket_destination(second_driver, second, cfg)

    first_pod = gke_container_entry(bundle_pod_labels(first.handle, cfg))
    second_pod = gke_container_entry(bundle_pod_labels(second.handle, cfg))
    unbound_pod = gke_container_entry({"astrolift.dev/app": "checkout"})

    assert router.destinations(first_pod) == {first_bucket}
    assert router.destinations(second_pod) == {second_bucket}
    # Not bound to any bundle: the project's _Default bucket, as before.
    assert router.destinations(unbound_pod) == {"_Default"}


def test_an_entry_carrying_the_bundle_id_anywhere_but_the_pod_label_is_not_routed() -> None:
    """Only the label GKE copies from the pod routes; a value in the payload, in
    ``resource.labels`` or on another resource type is just data."""
    driver, logging, handle, cfg = _bound()
    bundle = handle.handle.rsplit("/", 1)[-1]
    router = LogRouter(logging)
    entry = gke_container_entry({})
    entry["textPayload"] = bundle
    entry["resource"]["labels"][BUNDLE_POD_LABEL] = bundle
    api_written = {**gke_container_entry({BUNDLE_POD_LABEL: bundle}), "resource": {"type": "global", "labels": {}}}

    assert router.destinations(entry) == {"_Default"}
    assert _bucket_destination(driver, handle, cfg) not in router.destinations(api_written)


def test_a_bundle_from_before_2099_gains_its_route_on_the_next_reconcile() -> None:
    driver, logging, handle, cfg = _bound()
    logging.resources["log_sinks"].clear()
    logging.resources["log_exclusions"].clear()

    assert driver.update(UpdateSpec(handle=handle.handle, config=cfg, managed_service_id=MSID)).ok

    assert len(logging.resources["log_sinks"]) == len(logging.resources["log_exclusions"]) == 1


def test_a_tenant_cannot_declare_the_route_id() -> None:
    driver, _, handle, _ = _bound()
    bundle = handle.handle.rsplit("/", 1)[-1]

    result = driver.provision(
        _spec({"log_sinks": [{"id": f"{bundle}.route", "body": {"destination": "storage.googleapis.com/x"}}]}),
    )

    assert result.ok is False
    assert result.errors == ["invalid_cloud_operations_config"]


def test_deprovision_removes_the_route() -> None:
    driver, logging, handle, cfg = _bound()

    assert driver.deprovision(DeprovisionSpec(handle.handle, cfg, managed_service_id=MSID)).ok

    assert not logging.resources["log_sinks"] and not logging.resources["log_exclusions"]


# ----- tenant sinks and log metrics stay off other bundles ----------------


def test_a_tenant_sink_or_log_metric_cannot_select_another_bundles_entries() -> None:
    _, logging, handle, _ = _bound(
        {
            "log_sinks": [
                {"id": "export", "body": {"destination": "bigquery.googleapis.com/projects/acme-prod/datasets/logs"}},
            ],
            "log_metrics": [{"id": "errors", "body": {"filter": "severity>=ERROR"}}],
        },
    )
    bundle = handle.handle.rsplit("/", 1)[-1]
    # Only the bundle's own entries since #2419, not every unlabelled one.
    own = f'resource.type="k8s_container" AND {_ENTRY_LABEL}="{bundle}"'

    assert logging.resources["log_sinks"]["projects/acme-prod/sinks/export"]["filter"] == own
    metric = logging.resources["log_metrics"]["projects/acme-prod/metrics/errors"]
    assert metric["filter"] == f"(\nseverity>=ERROR\n)\nAND {own}"


def test_a_tenant_sink_on_one_bundle_never_receives_another_bundles_container_logs() -> None:
    warehouse = "bigquery.googleapis.com/projects/acme-prod/datasets/first"
    everything = {"destination": warehouse, "filter": 'resource.type="k8s_container"'}
    _, logging, first, cfg = _bound({"log_sinks": [{"id": "everything", "body": everything}]}, msid="managed-a")
    second_driver, _, second, _ = _bound(msid="managed-b", logging=logging)
    router = LogRouter(logging)

    first_pod = gke_container_entry(bundle_pod_labels(first.handle, cfg))
    second_pod = gke_container_entry(bundle_pod_labels(second.handle, {}))

    assert warehouse in router.destinations(first_pod)
    assert router.destinations(second_pod) == {_bucket_destination(second_driver, second, {})}


def test_a_tenant_sink_cannot_write_into_another_bundles_bucket() -> None:
    _, logging, _, _ = _bound(msid="managed-2")
    other_bucket = next(iter(logging.resources["log_bucket"]))
    driver, _, _ = _driver()

    result = driver.provision(
        _spec(
            {
                "deletion_protection": False,
                "log_sinks": [
                    {
                        "id": "inject",
                        "unique_writer_identity": False,
                        "body": {"destination": f"logging.googleapis.com/{other_bucket}"},
                    },
                ],
            },
        ),
    )

    assert result.ok is False
    assert "bundle's own" in result.message


# ----- the workload identity -----------------------------------------------


def test_an_existing_binding_migrates_off_the_project_on_its_next_reconcile() -> None:
    """End to end: the bundle binding's grants leave the identity holding no
    project-level logging or monitoring role."""
    driver, _, handle, cfg = _bound()
    other = "serviceAccount:someone-else@acme-prod.iam.gserviceaccount.com"
    project_iam = FakeIAMWithViews(
        [
            {"role": "roles/logging.viewer", "members": [_SA, other]},
            {"role": "roles/logging.logWriter", "members": [_SA]},
            {"role": "roles/monitoring.viewer", "members": [_SA]},
            {"role": "roles/monitoring.metricWriter", "members": [_SA]},
            {"role": "roles/storage.objectViewer", "members": [_SA]},
            {"role": "roles/logging.viewer", "members": [_SA], "condition": {"title": "operator"}},
        ],
    )
    identity = _identity(project_iam)
    permissions = _gcp_permissions(driver.binding(handle, cfg))

    identity.create_identity_role("app-identity", permissions)

    unconditional = {
        **project_iam.policy,
        "bindings": [item for item in project_iam.policy["bindings"] if not item.get("condition")],
    }
    assert not _project_roles_for(_SA, unconditional) & _PROJECT_WIDE
    assert _project_roles_for(_SA, unconditional) == {"roles/storage.objectViewer"}
    assert _project_roles_for(other, project_iam.policy) == {"roles/logging.viewer"}
    # A conditional binding is an operator's, not one this driver wrote.
    assert any(item.get("condition") for item in project_iam.policy["bindings"])
    view = permissions[0]["resource"]
    assert project_iam.views[view]["bindings"] == [{"role": "roles/logging.viewAccessor", "members": [_SA]}]

    sets = (len(project_iam.set_calls), len(project_iam.view_set_calls))
    identity.create_identity_role("app-identity", permissions)
    assert (len(project_iam.set_calls), len(project_iam.view_set_calls)) == sets


def test_a_role_another_binding_still_asks_for_is_kept() -> None:
    project_iam = FakeIAMWithViews([{"role": "roles/monitoring.viewer", "members": [_SA]}])

    _identity(project_iam).create_identity_role(
        "app-identity",
        [{"role": "roles/monitoring.viewer", "resource": "projects/acme-prod"}],
    )

    assert _project_roles_for(_SA, project_iam.policy) == {"roles/monitoring.viewer"}


@pytest.mark.parametrize(
    ("permission", "message"),
    [
        ({"role": "roles/logging.viewAccessor", "resource": "projects/acme-prod"}, "never on the project"),
        ({"role": "roles/logging.viewAccessor"}, "never on the project"),
        (
            {"role": "roles/logging.viewer", "resource": "projects/acme-prod/locations/global/buckets/b/views/v"},
            "grants only roles/logging.viewAccessor",
        ),
        (
            {"role": "roles/logging.viewAccessor", "resource": "projects/other/locations/global/buckets/b/views/v"},
            "not in project",
        ),
    ],
)
def test_a_log_view_reader_is_never_widened(permission: dict[str, Any], message: str) -> None:
    project_iam = FakeIAMWithViews()

    with pytest.raises(ValueError, match=message):
        _identity(project_iam).create_identity_role("app-identity", [permission])

    assert project_iam.set_calls == [] and project_iam.view_set_calls == []


def test_a_detached_bundles_view_grant_is_withdrawn_from_this_identity_only() -> None:
    driver, _, handle, cfg = _bound()
    other = "serviceAccount:other-app@acme-prod.iam.gserviceaccount.com"
    project_iam = FakeIAMWithViews()
    identity = _identity(project_iam)
    view = _gcp_permissions(driver.binding(handle, cfg))[0]["resource"]
    identity.create_identity_role("app-identity", [{"role": "roles/logging.viewAccessor", "resource": view}])
    project_iam.views[view]["bindings"] += [
        {"role": "roles/logging.viewAccessor", "members": [other]},
        {"role": "roles/logging.viewAccessor", "members": [_SA], "condition": {"title": "operator"}},
    ]

    assert identity.withdraw_log_view_grants("app-identity", [view]) == [view]

    assert project_iam.views[view]["bindings"] == [
        {"role": "roles/logging.viewAccessor", "members": [other]},
        {"role": "roles/logging.viewAccessor", "members": [_SA], "condition": {"title": "operator"}},
    ]
    assert project_iam.views[view]["etag"] == "view-etag"
    writes = len(project_iam.view_set_calls)
    assert identity.withdraw_log_view_grants("app-identity", [view]) == []
    assert len(project_iam.view_set_calls) == writes


def test_withdrawing_from_a_view_that_is_gone_is_a_no_op_but_other_errors_surface() -> None:
    project_iam = FakeIAMWithViews()
    identity = _identity(project_iam)
    gone = "projects/acme-prod/locations/global/buckets/gone/views/_AllLogs"
    denied = "projects/acme-prod/locations/global/buckets/denied/views/_AllLogs"
    project_iam.view_errors = {gone: 404, denied: 403}

    assert identity.withdraw_log_view_grants("app-identity", [gone]) == []
    with pytest.raises(ProviderError, match="HTTP 403"):
        identity.withdraw_log_view_grants("app-identity", [denied])
    assert project_iam.view_set_calls == []


@pytest.mark.parametrize(
    ("view", "message"),
    [
        ("projects/acme-prod", "is not a Cloud Logging view"),
        ("projects/other/locations/global/buckets/b/views/_AllLogs", "not in project"),
    ],
)
def test_withdrawal_touches_only_a_log_view_in_the_identitys_project(view: str, message: str) -> None:
    project_iam = FakeIAMWithViews()

    with pytest.raises(ValueError, match=message):
        _identity(project_iam).withdraw_log_view_grants("app-identity", [view])

    assert project_iam.set_calls == [] and project_iam.view_set_calls == []
