from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import pytest

from astrolift_lifecycle.models import Deployment
from astrolift_workflows.activities.image_retention import (
    release_obsolete_deployment_images,
    retain_deployment_images,
)

pytestmark = pytest.mark.django_db
DIGEST = "sha256:" + "a" * 64
REF = "123456789012.dkr.ecr.us-east-1.amazonaws.com/acme/api:sha-abc"


class RegistryFake:
    def __init__(self):
        self.released = []
        self.fail = False

    def _registry_uri(self):
        return "123456789012.dkr.ecr.us-east-1.amazonaws.com"

    def retain_deployment_images(self, refs, *, environment, deployment):
        if self.fail:
            raise RuntimeError("retention unavailable")
        return [
            {
                "repository": "acme/api",
                "tag": f"retain-astrolift-{UUID(environment).hex}-{UUID(deployment).hex}-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "digest": DIGEST,
                "source_ref": ref,
                "pinned_ref": ref.rsplit(":", 1)[0] + "@" + DIGEST,
            }
            for ref in set(refs)
        ]

    def release_deployment_images(self, pins, *, environment, deployment):
        self.released.append((environment, deployment, pins))


@pytest.fixture
def registry(cluster, monkeypatch):
    cluster.provider_plugin.slug = "aws"
    cluster.provider_plugin.save(update_fields=["slug"])
    fake = RegistryFake()
    monkeypatch.setattr("core.app_deploy.driver_for_capability", lambda *args: fake)
    return fake


def deployment(app, env, status="deploying"):
    return Deployment.objects.create(
        registered_app=app, app_environment=env, trigger_kind="manual", status=status, image_tag="sha-abc"
    )


def resources():
    return [
        {
            "kind": "Deployment",
            "spec": {
                "template": {
                    "spec": {
                        "containers": [{"name": "app", "image": REF}],
                        "initContainers": [{"name": "init", "image": REF}],
                    }
                }
            },
        }
    ]


def test_pins_saved_and_containers_digest_pinned(app, env, registry):
    d = deployment(app, env)
    manifests = resources()
    retain_deployment_images(d, manifests)
    d.refresh_from_db()
    assert len(d.config_snapshot["ecr_retention_pins"]) == 1
    pod = manifests[0]["spec"]["template"]["spec"]
    assert pod["containers"][0]["image"].endswith("@" + DIGEST)
    assert pod["initContainers"][0]["image"].endswith("@" + DIGEST)


def test_retry_and_rollback_do_not_copy_another_deployments_pins(app, env, registry):
    prior = deployment(app, env)
    retain_deployment_images(prior, resources())
    d = deployment(app, env)
    d.config_snapshot = dict(prior.config_snapshot)
    d.save(update_fields=["config_snapshot", "updated_at", "version"])
    retain_deployment_images(d, resources())
    retain_deployment_images(d, resources())
    d.refresh_from_db()
    pins = d.config_snapshot["ecr_retention_pins"]
    assert len(pins) == 1
    assert d.guid.hex in pins[0]["tag"]
    assert prior.guid.hex not in pins[0]["tag"]


def test_pin_failure_stops_before_cluster_apply(app, env, registry, monkeypatch):
    from astrolift_workflows.activities.app_lifecycle import _apply_manifests_sync

    d = deployment(app, env)
    registry.fail = True
    monkeypatch.setattr(
        "core.app_deploy.driver_for_deployment", lambda d: (object(), SimpleNamespace(slug="test"), "acme")
    )
    monkeypatch.setattr("core.app_deploy.render_resources_for_deployment", lambda d: resources())
    calls = []
    monkeypatch.setattr(
        "astrolift_workflows.activities.direct_apply.apply_with_dry_run", lambda *args: calls.append(args)
    )
    with pytest.raises(RuntimeError, match="retention unavailable"):
        _apply_manifests_sync(d.pk)
    assert calls == []


def test_latest_ten_successful_deployments_keep_their_pins(app, env, registry):
    rows = [deployment(app, env, "superseded") for _ in range(11)]
    for d in rows:
        retain_deployment_images(d, resources())
    release_obsolete_deployment_images(rows[-1])
    assert [item[1] for item in registry.released] == [str(rows[0].guid)]


def test_live_failed_and_inflight_pins_survive_even_if_old(app, env, registry):
    for status in ["running", "failed", "deploying", "pending"]:
        retain_deployment_images(deployment(app, env, status), resources())
    rows = [deployment(app, env, "superseded") for _ in range(10)]
    for d in rows:
        retain_deployment_images(d, resources())
    release_obsolete_deployment_images(rows[-1])
    assert registry.released == []


def test_environment_retirement_is_scoped(app, env, env_requires_approval, registry):
    other = deployment(app, env_requires_approval, "superseded")
    retain_deployment_images(other, resources())
    rows = [deployment(app, env, "superseded") for _ in range(11)]
    for d in rows:
        retain_deployment_images(d, resources())
    release_obsolete_deployment_images(rows[-1])
    assert [item[1] for item in registry.released] == [str(rows[0].guid)]


def test_non_aws_deployment_does_not_need_registry(app, env, monkeypatch):
    d = deployment(app, env)

    def unexpected(*args):
        raise AssertionError("non-AWS rollout must not resolve an ECR driver")

    monkeypatch.setattr("core.app_deploy.driver_for_capability", unexpected)
    retain_deployment_images(d, resources())
    release_obsolete_deployment_images(d)
    assert "ecr_retention_pins" not in d.config_snapshot


@pytest.mark.parametrize("has_containers", [True, False])
def test_external_image_rollback_drops_inherited_pins(app, env, registry, monkeypatch, has_containers):
    prior = deployment(app, env)
    retain_deployment_images(prior, resources())
    current = deployment(app, env)
    current.config_snapshot = {**prior.config_snapshot, "manifest_hash": "preserve-me"}
    current.save(update_fields=["config_snapshot", "updated_at", "version"])
    monkeypatch.setattr(registry, "retain_deployment_images", lambda *args, **kwargs: [])
    retain_deployment_images(current, resources() if has_containers else [{"kind": "Service"}])
    current.refresh_from_db()
    assert current.config_snapshot["ecr_retention_pins"] == []
    assert current.config_snapshot["manifest_hash"] == "preserve-me"
    prior.refresh_from_db()
    assert prior.config_snapshot["ecr_retention_pins"]


def test_successful_apply_receives_protected_digest(app, env, registry, monkeypatch):
    from astrolift_workflows.activities.app_lifecycle import _apply_manifests_sync

    current = deployment(app, env)
    monkeypatch.setattr(
        "core.app_deploy.driver_for_deployment", lambda d: (object(), SimpleNamespace(slug="test"), "acme")
    )
    monkeypatch.setattr("core.app_deploy.render_resources_for_deployment", lambda d: resources())
    applied = []

    def apply(driver, cluster, namespace, manifests):
        current.refresh_from_db()
        assert current.config_snapshot["ecr_retention_pins"]
        applied.extend(manifests)
        return SimpleNamespace(errors=[], created=["Deployment/app"], updated=[], unchanged=[])

    monkeypatch.setattr("astrolift_workflows.activities.direct_apply.apply_with_dry_run", apply)
    assert _apply_manifests_sync(current.pk)["created"] == ["Deployment/app"]
    pod = applied[0]["spec"]["template"]["spec"]
    assert pod["containers"][0]["image"].endswith("@" + DIGEST)
    assert pod["initContainers"][0]["image"].endswith("@" + DIGEST)


def test_running_snapshot_keeps_retention_evidence(app, env, registry):
    from astrolift_workflows.activities.app_lifecycle import _mark_running_sync

    current = deployment(app, env)
    retain_deployment_images(current, resources())
    pins = current.config_snapshot["ecr_retention_pins"]
    _mark_running_sync(current.pk)
    current.refresh_from_db()
    assert current.status == Deployment.Status.RUNNING
    assert current.config_snapshot["ecr_retention_pins"] == pins
    assert current.config_snapshot["image_tag"] == current.image_tag


@pytest.mark.parametrize("fail", [True, False])
def test_retention_gate_precedes_secret_deletion_and_writes(app, env, registry, monkeypatch, fail):
    from astrolift_workflows.activities.app_lifecycle import _update_secrets_sync

    current = deployment(app, env)
    registry.fail = fail
    calls = []

    class ClusterDriver:
        def apply_manifests(self, cluster_slug, namespace, manifests, *, dry_run=False):
            current.refresh_from_db()
            assert current.config_snapshot["ecr_retention_pins"]
            assert dry_run
            assert manifests[0]["spec"]["template"]["spec"]["containers"][0]["image"].endswith("@" + DIGEST)
            calls.append("dry_run")
            return SimpleNamespace(ok=True)

        def delete_manifests(self, *args, **kwargs):
            calls.append("delete")

    monkeypatch.setattr(
        "core.app_deploy.driver_for_deployment",
        lambda d: (ClusterDriver(), SimpleNamespace(slug="test"), "acme"),
    )
    monkeypatch.setattr("core.app_deploy.render_resources_for_deployment", lambda d: resources())
    if fail:
        with pytest.raises(RuntimeError, match="retention unavailable"):
            _update_secrets_sync(current.pk)
        assert calls == []
    else:
        assert _update_secrets_sync(current.pk) == 0
        assert calls == ["dry_run", "delete"]


def test_retry_freezes_source_digest_without_a_second_registry_write(app, env, registry):
    current = deployment(app, env)
    retain_deployment_images(current, resources())
    stale = Deployment.objects.get(pk=current.pk)
    registry.fail = True
    manifests = resources()
    retain_deployment_images(stale, manifests)
    assert manifests[0]["spec"]["template"]["spec"]["containers"][0]["image"].endswith("@" + DIGEST)


def test_already_pinned_ref_reuses_owned_snapshot(app, env, registry):
    from astrolift_workflows.activities.image_retention import retain_deployment_image_refs

    current = deployment(app, env)
    first = retain_deployment_image_refs(current, [REF])[REF]
    registry.fail = True
    assert retain_deployment_image_refs(current, [first]) == {first: first}


@pytest.mark.parametrize(
    "key,value", [("digest", "sha256:" + "b" * 64), ("pinned_ref", "evil.example/api@" + DIGEST)]
)
def test_inconsistent_snapshot_is_not_trusted_as_a_cached_pin(app, env, registry, key, value):
    current = deployment(app, env)
    retain_deployment_images(current, resources())
    current.config_snapshot["ecr_retention_pins"][0][key] = value
    current.save(update_fields=["config_snapshot", "updated_at", "version"])
    registry.fail = True
    with pytest.raises(RuntimeError, match="retention unavailable"):
        retain_deployment_images(current, resources())
