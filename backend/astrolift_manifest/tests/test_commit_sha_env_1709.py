"""A container can report the commit it is running (#1709).

The image tag the platform deploys IS the commit SHA, and nothing put it
anywhere the process could read. An app reading GIT_SHA reported "dev" while
running an image tagged with the commit -- the information was in the tag and
nowhere the process could see it.

That matters for confirming a rollout. The tag on a workload can be right while
the pod is stale, which is exactly what a stalled StatefulSet rollout looks like
(#1724), so the honest check is what the running container says about itself.
"""

from __future__ import annotations

from astrolift_manifest.render import render_manifests
from astrolift_manifest.types import ContainerManifest, NormalizedManifest, WorkloadManifest

SHA = "1dd8c35b21a69234610dc35015acc1c36af38534"


def _render(container: ContainerManifest, image_tag: str = SHA):
    manifest = NormalizedManifest(
        name="app",
        workloads=(WorkloadManifest(name="web", kind="deployment", containers=(container,)),),
        managed_services=(),
        defaults_applied=(),
        serialized={},
    )
    out = render_manifests(
        manifest,
        namespace="ns",
        image_tag=image_tag,
        image_repository="ghcr.io/acme/app",
        environment_name="prod",
    )
    dep = next(r for r in out if r["kind"] == "Deployment")
    return {e["name"]: e["value"] for e in dep["spec"]["template"]["spec"]["containers"][0]["env"]}


def test_both_conventional_names_carry_the_tag():
    env = _render(ContainerManifest(name="app", is_primary=True))
    assert env["ASTROLIFT_COMMIT"] == SHA
    assert env["GIT_SHA"] == SHA


def test_a_container_with_no_env_of_its_own_still_gets_them():
    """The workload previously rendered no env block at all, so there was
    nowhere for a version to appear."""
    env = _render(ContainerManifest(name="app", is_primary=True))
    assert env, "no env rendered"


def test_the_manifest_wins_over_the_injected_default():
    """An app that already sets GIT_SHA -- from its own build, say -- must keep
    its value. This is a default, not an override."""
    env = _render(ContainerManifest(name="app", is_primary=True, env=(("GIT_SHA", "from-the-app"),)))
    assert env["GIT_SHA"] == "from-the-app"
    assert env["ASTROLIFT_COMMIT"] == SHA


def test_the_manifest_env_is_preserved_alongside():
    env = _render(ContainerManifest(name="app", is_primary=True, env=(("DATABASE_URL", "postgres://x"),)))
    assert env["DATABASE_URL"] == "postgres://x"
    assert env["ASTROLIFT_COMMIT"] == SHA


def test_no_tag_injects_nothing():
    """An empty tag would advertise a version of "" -- worse than absent,
    because an app would report it as though it meant something."""
    env = _render(ContainerManifest(name="app", is_primary=True, env=(("A", "b"),)), image_tag="")
    assert "ASTROLIFT_COMMIT" not in env
    assert "GIT_SHA" not in env
