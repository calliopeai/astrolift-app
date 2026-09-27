"""The install writes the auth host's Secret, and nothing else sees it (#2055).

oauth2-proxy reads ``client-id``, ``client-secret`` and ``cookie-secret`` from
a Secret the recipe only names. On the first install it was made by hand. The
install now writes it from the cluster row, before the release that reads it,
without the values reaching the recipe, the run's result, the workflow
history, a log line or an error message.

The cluster row is real and so is the recipe (the EKS driver's, built with
recording AWS doubles). Only the tenant cluster's API server is faked.
"""

from __future__ import annotations

import base64
import copy
import json
import logging
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from _sdk.cluster import ApplyError, ApplyResult, DeleteResult
from asgiref.sync import sync_to_async
from aws.cluster_eks import EKSClusterDriver, EKSConfig
from k8s_native.central_auth import (
    CENTRAL_AUTH_SECRET_NAME,
    central_auth_secret_manifest,
    edge_cluster_issuer,
)

from astrolift_workflows.activities.install_prereqs import (
    _apply_post_install_manifests,
    _flux_crd_missing,
    _install_cluster_prereqs_sync,
)
from core.app_deploy import AppDeployError

COGNITO = {
    "discovery_url": "https://cognito-idp.us-west-2.amazonaws.com/us-west-2_abc/.well-known/openid-configuration",
    "client_id": "central-client-id",
    "auth_proxy_host": "auth.apps.example.net",
}
CLIENT_SECRET = "client-secret-2055-never-leaves"
COOKIE_SECRET = "cookie-secret-2055-never-leaves"
WITH_SECRETS = {**COGNITO, "client_secret": CLIENT_SECRET, "cookie_secret": COOKIE_SECRET}
STAGE_A = ["ingress-nginx", "cert-manager", "oauth2-proxy"]
DIGEST = "astrolift.io/central-auth-secret-digest"


def _secret_forms() -> list[str]:
    forms = []
    for secret in (CLIENT_SECRET, COOKIE_SECRET):
        forms += [secret, base64.b64encode(secret.encode()).decode()]
    return forms


def _eks_recipe_driver() -> EKSClusterDriver:
    sts = MagicMock()
    sts.get_caller_identity.return_value = {"Account": "123456789012"}
    eks = MagicMock()
    eks.describe_cluster.return_value = {"cluster": {"resourcesVpcConfig": {"vpcId": "vpc-123"}}}
    ec2 = MagicMock()
    ec2.describe_security_groups.return_value = {"SecurityGroups": []}
    return EKSClusterDriver(
        config=EKSConfig(region="us-west-2", cluster_name="astrolift-eks"),
        eks_client=eks,
        sts_client=sts,
        ec2_client=ec2,
        k8s_client_factory=lambda **kw: MagicMock(),
    )


class _ClusterApi:
    """The tenant cluster's API server. Records every apply; answers GETs
    from ``existing``; refuses the Secret with ``secret_errors``. The
    recipe is EKS's, less any key in ``without``."""

    def __init__(
        self,
        *,
        existing: dict | None = None,
        secret_errors: list | None = None,
        without: frozenset[str] = frozenset(),
    ) -> None:
        self.applied: list[tuple[str, list[dict[str, Any]]]] = []
        self.gets: list[tuple[str, str, str]] = []
        self._existing = existing or {}
        self._secret_errors = secret_errors
        self._without = without
        self._eks = _eks_recipe_driver()

    def bootstrap_components(self, ctx):  # noqa: ANN001
        return [c for c in self._eks.bootstrap_components(ctx) if c.key not in self._without]

    def apply_manifests(self, cluster, namespace, manifests, *, dry_run=False):  # noqa: ANN001
        batch = copy.deepcopy(list(manifests))
        self.applied.append((namespace, batch))
        if self._secret_errors and any(m.get("kind") == "Secret" for m in batch):
            return ApplyResult(created=[], updated=[], unchanged=[], errors=list(self._secret_errors))
        return ApplyResult(
            created=[f"{m['kind']}/{m['metadata']['name']}" for m in batch],
            updated=[],
            unchanged=[],
            errors=[],
        )

    def delete_manifests(self, cluster, namespace, manifests, *, propagation_policy=None):  # noqa: ANN001
        return DeleteResult(
            deleted=[], not_found=[f"{m['kind']}/{m['metadata']['name']}" for m in manifests], errors=[]
        )

    def get_manifest(self, cluster, namespace, kind, name):  # noqa: ANN001
        self.gets.append((namespace, kind, name))
        return self._existing.get((namespace, kind, name))

    @property
    def secret_writes(self) -> list[dict[str, Any]]:
        return [m for _ns, batch in self.applied for m in batch if m.get("kind") == "Secret"]

    def releases(self) -> dict[str, dict[str, Any]]:
        return {
            m["metadata"]["name"]: m
            for _ns, batch in self.applied
            for m in batch
            if m.get("kind") == "HelmRelease"
        }


@pytest.fixture
def cluster_api(monkeypatch):
    def _install(api: _ClusterApi) -> _ClusterApi:
        monkeypatch.setattr("core.cluster_management._driver_for_cluster", lambda cluster: api)
        return api

    return _install


def _configure(cluster, oidc: dict | None, *, ingress_class: str = "alb"):
    cluster.ingress_class = ingress_class
    cluster.oidc_auth_config = oidc
    cluster.save()
    return cluster


@pytest.fixture
def captured_logs():
    """Every record, including those from loggers that do not propagate to
    the root logger ``caplog`` listens on."""
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Capture(level=logging.DEBUG)
    loggers = [logging.getLogger()] + [
        lg for lg in logging.root.manager.loggerDict.values() if isinstance(lg, logging.Logger)
    ]
    saved = [(lg, lg.level) for lg in loggers]
    for lg in loggers:
        lg.setLevel(logging.DEBUG)
        if lg is loggers[0] or not lg.propagate:
            lg.addHandler(handler)
    try:
        yield records
    finally:
        for lg, level in saved:
            lg.removeHandler(handler)
            lg.setLevel(level)


# ---- the Secret is written, first -----------------------------------


@pytest.mark.django_db
def test_the_secret_lands_before_the_release_that_reads_it(cluster, cluster_api):
    _configure(cluster, WITH_SECRETS)
    api = cluster_api(_ClusterApi())

    _install_cluster_prereqs_sync(cluster.pk, STAGE_A, {})

    namespace, first_batch = api.applied[0]
    assert namespace == "astrolift-system"
    assert [m["kind"] for m in first_batch] == ["Secret"]
    secret = first_batch[0]
    assert secret["metadata"] == {
        "name": CENTRAL_AUTH_SECRET_NAME,
        "namespace": "astrolift-system",
        "labels": {"astrolift.io/managed-by": "platform", "astrolift.io/bootstrap-component": "oauth2-proxy"},
    }
    assert {k: base64.b64decode(v).decode() for k, v in secret["data"].items()} == {
        "client-id": COGNITO["client_id"],
        "client-secret": CLIENT_SECRET,
        "cookie-secret": COOKIE_SECRET,
    }
    assert "astrolift-oauth2-proxy" in api.releases()
    assert len(api.secret_writes) == 1


@pytest.mark.django_db
def test_the_edge_issuer_is_applied_after_the_releases(cluster, cluster_api):
    _configure(cluster, {**WITH_SECRETS, "acme_email": "ops@example.net"})
    api = cluster_api(_ClusterApi())

    _install_cluster_prereqs_sync(cluster.pk, STAGE_A, {})

    issuers = [m for _ns, batch in api.applied for m in batch if m.get("kind") == "ClusterIssuer"]
    assert issuers == [edge_cluster_issuer("ops@example.net")]


@pytest.mark.django_db
def test_a_rewritten_secret_rolls_the_proxy(cluster, cluster_api):
    """Pods read the Secret once, at start. Rotating a secret or fixing a
    typo must roll them; an unchanged re-run must not."""
    _configure(cluster, WITH_SECRETS)

    def _digest() -> str:
        api = cluster_api(_ClusterApi())
        _install_cluster_prereqs_sync(cluster.pk, STAGE_A, {})
        return api.releases()["astrolift-oauth2-proxy"]["spec"]["values"]["podAnnotations"][DIGEST]

    first = _digest()
    assert _digest() == first

    _configure(cluster, {**WITH_SECRETS, "cookie_secret": "rotated-cookie-secret-2055"})
    assert _digest() != first


@pytest.mark.django_db
def test_the_digest_is_keyed(cluster, cluster_api, settings):
    """Anyone who can read the pod spec reads the annotation."""
    _configure(cluster, WITH_SECRETS)
    api = cluster_api(_ClusterApi())
    _install_cluster_prereqs_sync(cluster.pk, STAGE_A, {})
    first = api.releases()["astrolift-oauth2-proxy"]["spec"]["values"]["podAnnotations"][DIGEST]

    settings.SECRET_KEY = "another-platform-key-2055"
    api = cluster_api(_ClusterApi())
    _install_cluster_prereqs_sync(cluster.pk, STAGE_A, {})
    assert api.releases()["astrolift-oauth2-proxy"]["spec"]["values"]["podAnnotations"][DIGEST] != first


# ---- and it goes nowhere else ---------------------------------------


@pytest.mark.django_db
def test_no_secret_value_leaves_the_activity(cluster, cluster_api, captured_logs):
    _configure(cluster, WITH_SECRETS)
    api = cluster_api(_ClusterApi())

    result = _install_cluster_prereqs_sync(cluster.pk, STAGE_A, {})

    everything_else = [m for _ns, batch in api.applied for m in batch if m.get("kind") != "Secret"]
    logged = "\n".join(r.getMessage() + repr(r.__dict__) for r in captured_logs)
    for form in _secret_forms():
        assert form not in json.dumps(result), "the result is recorded in workflow history"
        assert form not in json.dumps(everything_else), "only the Secret may carry the values"
        assert form not in logged
    # The capture is live: the activity's own line about the Secret is in it.
    assert any(CENTRAL_AUTH_SECRET_NAME in r.getMessage() for r in captured_logs)


@pytest.mark.django_db
def test_a_refused_write_is_reported_without_the_values(cluster, cluster_api):
    """The apiserver's reply is carried verbatim into the run's error, which
    operators read. Whatever it echoes, the values do not come back out."""
    _configure(cluster, WITH_SECRETS)
    echoed = ApplyError(
        kind="Secret",
        name=CENTRAL_AUTH_SECRET_NAME,
        namespace="astrolift-system",
        exception_type="ApiException",
        exception_message=(
            f"(422) body: {{'data': {{'client-secret': '{base64.b64encode(CLIENT_SECRET.encode()).decode()}'}}, "
            f"'stringData': {{'cookie-secret': '{COOKIE_SECRET}'}}}}"
        ),
        is_retryable=False,
    )
    api = cluster_api(_ClusterApi(secret_errors=[echoed]))

    with pytest.raises(AppDeployError) as caught:
        _install_cluster_prereqs_sync(cluster.pk, STAGE_A, {})

    message = str(caught.value)
    assert CENTRAL_AUTH_SECRET_NAME in message
    for form in _secret_forms():
        assert form not in message
    assert [m["kind"] for _ns, batch in api.applied for m in batch] == ["Secret"], "nothing else is applied"


# ---- when the row cannot supply it ----------------------------------


@pytest.mark.django_db
def test_a_hand_made_secret_is_kept_when_the_row_has_no_secrets(cluster, cluster_api):
    """The first install's Secret was made by hand, and its row carries no
    client_secret. Its re-runs must keep working and leave that Secret be."""
    _configure(cluster, {**COGNITO, "cookie_secret": COOKIE_SECRET})
    existing = {("astrolift-system", "Secret", CENTRAL_AUTH_SECRET_NAME): {"kind": "Secret"}}
    api = cluster_api(_ClusterApi(existing=existing))

    _install_cluster_prereqs_sync(cluster.pk, STAGE_A, {})

    assert api.secret_writes == []
    values = api.releases()["astrolift-oauth2-proxy"]["spec"]["values"]
    assert DIGEST not in (values.get("podAnnotations") or {})


@pytest.mark.django_db
def test_refused_before_anything_is_applied_when_no_secret_can_exist(cluster, cluster_api):
    _configure(cluster, {**COGNITO, "cookie_secret": COOKIE_SECRET})
    api = cluster_api(_ClusterApi())

    with pytest.raises(AppDeployError) as caught:
        _install_cluster_prereqs_sync(cluster.pk, STAGE_A, {})

    message = str(caught.value)
    assert "client_secret" in message
    assert "cookie_secret" not in message, "only what is missing is named"
    for form in _secret_forms():
        assert form not in message
    assert api.applied == []


@pytest.mark.django_db
def test_nothing_is_written_when_the_auth_host_is_not_selected(cluster, cluster_api):
    _configure(cluster, WITH_SECRETS)
    api = cluster_api(_ClusterApi())

    _install_cluster_prereqs_sync(cluster.pk, ["ingress-nginx", "cert-manager"], {})

    assert api.secret_writes == []
    assert api.gets == []


@pytest.mark.django_db
def test_nothing_is_written_for_a_recipe_without_the_auth_host(cluster, cluster_api):
    """The selection is free text. Naming oauth2-proxy on a cloud whose
    recipe has no such component renders no release to read a Secret."""
    _configure(cluster, WITH_SECRETS)
    api = cluster_api(_ClusterApi(without=frozenset({"oauth2-proxy"})))

    _install_cluster_prereqs_sync(cluster.pk, STAGE_A, {})

    assert api.secret_writes == []
    assert api.gets == []


# ---- dependencies Flux could never meet -----------------------------


@pytest.mark.django_db
def test_a_dependency_the_run_does_not_render_is_dropped(cluster, cluster_api):
    """The run deletes every recipe release it does not render, so a
    dependsOn naming one holds the dependent forever. Here: a load balancer
    controller and a Prometheus the cluster runs outside Flux, and a Dex the
    Cognito-backed auth host does not use."""
    _configure(cluster, {**WITH_SECRETS, "kind": "dex"})
    api = cluster_api(_ClusterApi())

    _install_cluster_prereqs_sync(cluster.pk, STAGE_A, {})

    releases = api.releases()
    assert releases["astrolift-oauth2-proxy"]["spec"]["dependsOn"] == [
        {"name": "astrolift-ingress-nginx", "namespace": "astrolift-system"}
    ]
    assert "dependsOn" not in releases["astrolift-ingress-nginx"]["spec"]
    assert "dependsOn" not in releases["astrolift-cert-manager"]["spec"]


@pytest.mark.django_db
def test_an_alb_only_install_renders_what_it_did(cluster, cluster_api):
    """The default selection on a cluster without the edge: no edge
    release, no Secret, no GET for one, and every dependency still named."""
    _configure(cluster, None)
    api = cluster_api(_ClusterApi())
    recipe = {c.key: c for c in api.bootstrap_components(_context(cluster))}
    defaults = [key for key, c in recipe.items() if c.default_enabled]

    _install_cluster_prereqs_sync(cluster.pk, defaults, {})

    releases = api.releases()
    assert "astrolift-ingress-nginx" not in releases
    assert api.secret_writes == []
    assert api.gets == []
    for key in defaults:
        rendered = releases.get(f"astrolift-{key}")
        if rendered is None:
            continue
        named = [d["name"] for d in rendered["spec"].get("dependsOn", [])]
        assert named == [f"astrolift-{dep}" for dep in recipe[key].depends_on]


def _context(cluster):
    from core.cluster_management import _context_for_cluster

    return _context_for_cluster(cluster)


# ---- post-install objects wait for their operator -------------------


def _issuer_component():
    from _sdk.cluster import BootstrapComponent

    return BootstrapComponent(
        key="cert-manager",
        title="cert-manager",
        default_enabled=True,
        rationale="",
        post_install_manifests=[edge_cluster_issuer()],
    )


class _WebhookStarting:
    def apply_manifests(self, cluster, namespace, manifests, *, dry_run=False):  # noqa: ANN001
        return ApplyResult(
            created=[],
            updated=[],
            unchanged=[],
            errors=[
                ApplyError(
                    kind="ClusterIssuer",
                    name="letsencrypt-prod",
                    namespace=namespace,
                    exception_type="ApiException",
                    exception_message=(
                        'Internal error occurred: failed calling webhook "webhook.cert-manager.io": '
                        "no endpoints available for service"
                    ),
                    is_retryable=False,
                )
            ],
        )


def test_an_issuer_sent_while_the_webhook_starts_is_retried():
    """The ClusterIssuer is applied right after the cert-manager release. A
    webhook with no endpoint yet is the operator not being up, like a CRD not
    yet registered, not a failure to shrug off."""
    not_ready, errors = _apply_post_install_manifests(
        _WebhookStarting(), "s", [_issuer_component()], {"cert-manager"}, "astrolift-system"
    )
    assert not_ready is True
    assert errors


def test_a_webhook_error_does_not_bootstrap_flux():
    """The Flux CRD test decides whether to install Flux. A webhook error on
    a post-install object must not send it there."""
    result = _WebhookStarting().apply_manifests("s", "astrolift-system", [])
    assert _flux_crd_missing(result.errors) is False


# ---- the workflow history -------------------------------------------


def _payload_texts(node: Any) -> list[str]:
    """Every payload in a history, decoded. The JSON form base64-encodes
    payload data, so searching it raw would find nothing either way."""
    texts: list[str] = []
    if isinstance(node, dict):
        if "data" in node and "metadata" in node and isinstance(node["data"], str):
            texts.append(base64.b64decode(node["data"]).decode("utf-8", "replace"))
        for value in node.values():
            texts += _payload_texts(value)
    elif isinstance(node, list):
        for value in node:
            texts += _payload_texts(value)
    return texts


@pytest.mark.django_db(transaction=True)
async def test_the_workflow_history_carries_no_secret(temporal_env, monkeypatch, cluster):
    from astrolift_workflows.activities import install_cluster_prereqs, record_cluster_bootstrap_run
    from astrolift_workflows.inputs import Actor, InstallClusterPrereqsInput
    from astrolift_workflows.workflows.install_cluster_prereqs import InstallClusterPrereqsWorkflow
    from core.testing.temporal import temporal_worker

    await sync_to_async(_configure)(cluster, WITH_SECRETS)
    api = _ClusterApi()
    monkeypatch.setattr("core.cluster_management._driver_for_cluster", lambda c: api)

    async with temporal_worker(
        temporal_env,
        workflows=[InstallClusterPrereqsWorkflow],
        activities=[install_cluster_prereqs, record_cluster_bootstrap_run],
    ):
        handle = await temporal_env.client.start_workflow(
            InstallClusterPrereqsWorkflow.run,
            InstallClusterPrereqsInput(
                cluster_id=cluster.pk,
                actor=Actor(kind="system"),
                selected_components=tuple(STAGE_A),
                option_overrides={},
                organization_id=cluster.organization_id,
            ),
            id=f"install-prereqs-2055-{uuid4()}",
            task_queue="astrolift-test",
        )
        result = await handle.result()
        history = await handle.fetch_history()

    assert result.ok is True, result.message
    assert len(api.secret_writes) == 1, "the Secret did reach the cluster"
    raw = history.to_json()
    decoded = "\n".join(_payload_texts(json.loads(raw)))
    assert "astrolift-system" in decoded, "the payloads were decoded"
    for form in _secret_forms():
        assert form not in raw
        assert form not in decoded


def test_the_manifest_the_test_expects_is_the_one_the_activity_builds():
    """Guards the fixtures above against drifting from the builder."""
    manifest = central_auth_secret_manifest(WITH_SECRETS, namespace="astrolift-system")
    assert manifest is not None
    assert set(manifest["data"]) == {"client-id", "client-secret", "cookie-secret"}
