"""Real PostgreSQL, native Kubernetes and ASGI exec receipts; no transport or auth replacements."""

import time
from uuid import uuid4

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from astrolift_identity.api_tokens import mint_token
from astrolift_identity.models import ApiToken
from core.exec_targets import ExecTargetError, admit_exec_target, review_exec_target
from core.permissions import Permission
from core.schema.exec_ws import exec_ws_application
from core.tests import test_workload_environment_contracts_2217 as shared_targets
from core.tests.test_workload_environment_contracts_2217 import subject
from core.tests.utils.scope_world import bind_role

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.timeout(30, method="thread")]


@pytest.fixture
def world():
    return shared_targets.world.__wrapped__()


@pytest.fixture
def kind_targets(world):
    yield from shared_targets.kind_targets.__wrapped__(world)


@pytest.fixture
def exec_targets(world, kind_targets):
    import os

    for letter, cluster in zip("AB", (world.cluster_a, world.cluster_b), strict=True):
        with open(os.environ[f"ASTROLIFT_WORKLOAD_TEST_KUBECONFIG_{letter}"]) as stream:
            cluster.auth_config = {
                "kubeconfig": stream.read(),
                "context": f"kind-astrolift-env-target-2217-{letter.lower()}",
            }
        cluster.save()
    world.exec_binding = bind_role(
        world.user,
        permissions=[Permission.APP_EXEC_POD, Permission.APP_READ_LOGS],
        kind="APP",
        scope_id=world.medops_app.pk,
        slug="exec-target-authority",
    )
    for environment, api in kind_targets.bindings:
        api.core.create_namespaced_pod(
            environment.k8s_namespace,
            {
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {
                    "name": "reviewed-pod",
                    "labels": {
                        "astrolift.dev/app": world.medops_app.slug,
                        "astrolift.dev/environment": environment.name,
                        "astrolift.dev/workload": world.workload.slug,
                    },
                },
                "spec": {
                    "containers": [
                        {
                            "name": "main",
                            "image": "busybox:1.36",
                            "imagePullPolicy": "Never",
                            "command": ["sh", "-c", "sleep 600"],
                            "env": [{"name": "PROOF_ENV", "value": environment.name}],
                        }
                    ]
                },
            },
        )
    deadline = time.monotonic() + 20
    while not all(
        api.core.read_namespaced_pod("reviewed-pod", environment.k8s_namespace).status.phase == "Running"
        for environment, api in kind_targets.bindings
    ):
        if time.monotonic() >= deadline:
            pytest.fail("Task-owned exec proof pods did not become Running")
        time.sleep(0.1)
    yield kind_targets


def review(world, environment):
    with subject(world):
        target = review_exec_target(
            app_slug=world.medops_app.slug,
            workload_slug=world.workload.slug,
            environment_guid=str(environment.guid),
            pod_name="reviewed-pod",
            container="main",
        )
    return {
        key.split("_")[0] + "".join(piece.title() for piece in key.split("_")[1:]): value
        for key, value in target.facts.items()
    }


def credentials(world, scopes=None):
    issued = mint_token()
    row = ApiToken.objects.create(
        user=world.user,
        organization=world.org,
        name="Real exec proof",
        token_hash=issued.token_hash,
        scopes=scopes or ["read:apps", "write:apps"],
    )
    return row, {
        "Authorization": f"Bearer {issued.plaintext}",
        "X-Astrolift-Organization": str(world.org.guid),
    }


def receive_until(ws, kind):
    output = ""
    for _ in range(30):
        frame = ws.receive_json()
        if frame.get("type") in {"stdout", "stderr"}:
            output += frame.get("data", "")
        if frame.get("type") == kind:
            return frame, output
        if frame.get("type") in {"error", "exit"}:
            pytest.fail(f"Exec ended before expected {kind} frame: {frame.get('type')}")
    pytest.fail("Expected exec frame was not emitted")


@pytest.mark.parametrize("selected", ["selected", "remote"])
def test_real_exec_admits_only_exact_environment_and_ends_on_disconnect(world, exec_targets, selected):
    environment = getattr(world, selected)
    target = review(world, environment)
    _, headers = credentials(world)
    path = f"/app/exec/{world.medops_app.slug}/reviewed-pod?environmentId={environment.guid}"
    client = TestClient(exec_ws_application)
    with client.websocket_connect(path, headers=headers) as ws:
        ws.send_json(
            {
                "type": "open",
                "target": target,
                "container": "main",
                "tty": False,
                "command": ["sh", "-c", 'echo "$PROOF_ENV"; cat'],
            }
        )
        ready, output = receive_until(ws, "ready")
        assert ready["target"] == target
        assert ready["sessionId"] and not ready["resumable"] and not ready["inputReplay"]
        assert ready["target"]["podBinding"] == "PREFLIGHT_ONLY"
        marker = f"input-{uuid4().hex}"
        ws.send_json({"type": "stdin", "sessionId": str(uuid4()), "data": "never-forwarded\n"})
        error, rejected_output = receive_until(ws, "error")
        output += rejected_output
        assert "session ended" in error["message"]
        ws.send_json({"type": "stdin", "sessionId": ready["sessionId"], "data": marker + "\n"})
        while marker not in output:
            frame = ws.receive_json()
            output += frame.get("data", "")
        assert environment.name in output
        assert "never-forwarded" not in output
        previous_session = ready["sessionId"]
    # A new connection is empty and never adopts the previous command or stdin.
    with client.websocket_connect(path, headers=headers) as ws:
        ws.send_json({"type": "replay"})
        replay, _ = receive_until(ws, "replay")
        assert replay["sessionId"] != previous_session and replay["lines"] == []
        ws.send_json({"type": "open", "resumeSessionId": previous_session, "target": target})
        error, _ = receive_until(ws, "error")
        assert "unavailable" in error["message"]
        for unsupported in ({"actionAdmissionProof": "opaque-proof"}, {"resumable": True}):
            ws.send_json({"type": "open", "target": target, **unsupported})
            error, _ = receive_until(ws, "error")
            assert "unavailable" in error["message"]
        ws.send_json({"type": "stdin", "data": marker + "\n"})
        error, _ = receive_until(ws, "error")
        assert "no session" in error["message"]


@pytest.mark.parametrize("change", ["cluster", "namespace", "deleted", "container", "uid", "atomic", "proof"])
def test_exact_exec_target_refuses_stale_or_unsupported_authority(world, exec_targets, change):
    target = review(world, world.selected)
    if change == "cluster":
        type(world.selected).objects.filter(pk=world.selected.pk).update(tenant_cluster=world.cluster_b)
    elif change == "namespace":
        type(world.selected).objects.filter(pk=world.selected.pk).update(k8s_namespace="wrong-namespace")
    elif change == "deleted":
        world.selected.delete()
    elif change == "container":
        target["container"] = "other"
    elif change == "uid":
        target["podUid"] = str(uuid4())
    elif change == "atomic":
        target["requireAtomicPodBinding"] = True
    else:
        target["actionAdmissionProof"] = "unsupported-proof"
    with subject(world), pytest.raises(ExecTargetError):
        admit_exec_target(
            app_slug=world.medops_app.slug, pod_name="reviewed-pod", container="main", review=target
        )


def test_real_ws_revocation_before_input_closes_connection(world, exec_targets):
    target = review(world, world.selected)
    token, headers = credentials(world)
    path = f"/app/exec/{world.medops_app.slug}/reviewed-pod?environmentId={world.selected.guid}"
    with TestClient(exec_ws_application).websocket_connect(path, headers=headers) as ws:
        ws.send_json(
            {"type": "open", "target": target, "container": "main", "tty": False, "command": ["cat"]}
        )
        ready, _ = receive_until(ws, "ready")
        ApiToken.objects.filter(pk=token.pk).update(is_revoked=True)
        ws.send_json({"type": "stdin", "sessionId": ready["sessionId"], "data": "refused input\n"})
        with pytest.raises(WebSocketDisconnect) as disconnect:
            ws.receive_json()
        assert disconnect.value.code == 4401


def test_read_only_bearer_is_refused_before_websocket_accept(world, exec_targets):
    _, headers = credentials(world, ["read:apps"])
    path = f"/app/exec/{world.medops_app.slug}/reviewed-pod?environmentId={world.selected.guid}"
    with pytest.raises(WebSocketDisconnect) as disconnect:
        with TestClient(exec_ws_application).websocket_connect(path, headers=headers):
            pytest.fail("Read-only token must not open an exec connection")
    assert disconnect.value.code == 4403


def test_recreated_same_name_pod_requires_a_new_review(world, exec_targets):
    from kubernetes import client
    from kubernetes.client.exceptions import ApiException

    target = review(world, world.selected)
    api = exec_targets.apis[0].core
    namespace = world.selected.k8s_namespace
    old = api.read_namespaced_pod("reviewed-pod", namespace)
    api.delete_namespaced_pod(
        "reviewed-pod",
        namespace,
        grace_period_seconds=0,
        body=client.V1DeleteOptions(
            grace_period_seconds=0, preconditions=client.V1Preconditions(uid=old.metadata.uid)
        ),
    )
    deadline = time.monotonic() + 15
    while True:
        try:
            api.read_namespaced_pod("reviewed-pod", namespace)
        except ApiException as exc:
            assert exc.status == 404
            break
        assert time.monotonic() < deadline
        time.sleep(0.05)
    replacement = api.create_namespaced_pod(
        namespace,
        client.V1Pod(
            api_version="v1",
            kind="Pod",
            metadata=client.V1ObjectMeta(name="reviewed-pod", labels=old.metadata.labels),
            spec=old.spec,
        ),
    )
    assert replacement.metadata.uid != target["podUid"]
    with subject(world), pytest.raises(ExecTargetError, match="changed"):
        admit_exec_target(
            app_slug=world.medops_app.slug, pod_name="reviewed-pod", container="main", review=target
        )


@pytest.mark.parametrize("revocation", ["mapping", "grant"])
def test_current_admission_revocation_prevents_shell_input_effect(world, exec_targets, revocation):
    import os
    import subprocess

    target = review(world, world.selected)
    _, headers = credentials(world)
    path = f"/app/exec/{world.medops_app.slug}/reviewed-pod?environmentId={world.selected.guid}"
    marker = f"/refused-{uuid4().hex}"
    with TestClient(exec_ws_application).websocket_connect(path, headers=headers) as ws:
        ws.send_json({"type": "open", "target": target, "container": "main", "tty": False, "command": ["sh"]})
        ready, _ = receive_until(ws, "ready")
        if revocation == "mapping":
            type(world.selected).objects.filter(pk=world.selected.pk).update(tenant_cluster=world.cluster_b)
        else:
            world.exec_binding.delete()
        ws.send_json({"type": "stdin", "sessionId": ready["sessionId"], "data": f"touch {marker}\n"})
        with pytest.raises(WebSocketDisconnect) as disconnect:
            while True:
                ws.receive_json()
        assert disconnect.value.code == 4403
    receipt = subprocess.run(
        [
            "kubectl",
            "--kubeconfig",
            os.environ["ASTROLIFT_WORKLOAD_TEST_KUBECONFIG_A"],
            "--context",
            "kind-astrolift-env-target-2217-a",
            "-n",
            target["namespace"],
            "exec",
            "reviewed-pod",
            "-c",
            "main",
            "--",
            "test",
            "!",
            "-e",
            marker,
        ],
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert receipt.returncode == 0, "Refused input reached the pod"


@pytest.mark.parametrize("identity", ["appId", "workloadId", "environmentId"])
def test_foreign_review_identity_never_retargets_exec(world, exec_targets, identity):
    target = review(world, world.selected)
    target[identity] = str(uuid4())
    with subject(world), pytest.raises(ExecTargetError):
        admit_exec_target(
            app_slug=world.medops_app.slug, pod_name="reviewed-pod", container="main", review=target
        )


def test_real_http_exec_review_uses_selected_environment_and_current_scope(
    world, exec_targets, client, settings
):
    target = review(world, world.remote)
    _, headers = credentials(world)
    query = """query Exec($app: String!, $workload: String!, $env: GUID!) {
      astroliftAppExecTarget(appSlug: $app, workloadSlug: $workload, environmentId: $env,
                            podName: "reviewed-pod", container: "main") {
        appId workloadId workloadVersion appVersion environmentId environmentName environmentVersion
        clusterId clusterVersion namespace podName podUid container podBinding resumable
      }
    }"""
    response = client.post(
        f"/{settings.BASE_URL}gql/config/",
        content_type="application/json",
        data={
            "query": query,
            "variables": {
                "app": world.medops_app.slug,
                "workload": world.workload.slug,
                "env": str(world.remote.guid),
            },
        },
        HTTP_AUTHORIZATION=headers["Authorization"],
        HTTP_X_ASTROLIFT_ORGANIZATION=headers["X-Astrolift-Organization"],
    )
    body = response.json()
    assert response.status_code == 200 and not body.get("errors"), body
    assert body["data"]["astroliftAppExecTarget"] == target
