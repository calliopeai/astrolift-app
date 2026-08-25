"""A structurally impossible step fails at render, not in the pod (#1584).

`_build_job_manifest` renders every cluster-run pipeline job with
`privileged: False`, `allowPrivilegeEscalation: False`, `runAsNonRoot: True`
and no docker socket. So `astrolift/docker-build` has no daemon to reach,
cannot start one, and cannot escalate to try -- and the usual escapes are
ruled out by the same spec: kaniko needs root, rootless buildah needs user
namespaces plus `/dev/fuse` and seccomp allowances the spec does not grant.

The action rendered `docker build` commands anyway, so a pipeline using it
started a pod that ran and died with a docker-not-found error, several minutes
and one confusing log later.

This is the issue's own option 2, and it does not foreclose option 1: if a
rootless builder ever ships as a first-class capability, the entry comes out
of the map and the action starts working.
"""

from __future__ import annotations

import pytest

from astrolift_pipelines.step_script import StepScriptError, render_step_script

#: Required inputs per built-in, so a test that means to exercise the refusal
#: does not trip the input validator first and pass for the wrong reason.
REQUIRED = {
    "astrolift/docker-build": {"tags": "ghcr.io/acme/hello:v1"},
    "astrolift/git-checkout": {"repository": "https://github.com/acme/hello.git", "ref": "main"},
    "astrolift/kubectl-apply": {"manifest": "k8s/"},
}


class _Step:
    def __init__(self, uses="", run="", step_id="s1", position=1):
        self.position = position
        self.step_id = step_id
        self.uses = uses
        self.run = run
        self.env = {}
        self.with_params = dict(REQUIRED.get(str(uses).split("@", 1)[0], {}))


CLUSTER = {"cluster_run": True}


def test_a_docker_build_step_is_refused_in_a_cluster_job():
    with pytest.raises(StepScriptError) as exc:
        render_step_script([_Step(uses="astrolift/docker-build@v1")], context=CLUSTER)

    message = str(exc.value)
    assert "docker socket" in message
    assert "self-hosted runner" in message


def test_the_refusal_names_a_next_step():
    """An error that only says no costs the operator the same time the pod
    would have. This one has to say what to do instead."""
    with pytest.raises(StepScriptError) as exc:
        render_step_script([_Step(uses="astrolift/docker-build@v1")], context=CLUSTER)

    assert "astro ci" in str(exc.value)


def test_the_version_suffix_does_not_evade_it():
    """`@v1`, `@v2`, bare -- all the same action."""
    for uses in ("astrolift/docker-build", "astrolift/docker-build@v1", "astrolift/docker-build@v9"):
        with pytest.raises(StepScriptError):
            render_step_script([_Step(uses=uses)], context=CLUSTER)


# ---- what must keep working ---------------------------------------------


def test_a_self_hosted_render_still_allows_it():
    """The one path that works today. A self-hosted runner has a daemon and
    its own security context, so refusing there would break it -- which is
    why the default is allow and the spawner opts in."""
    script = render_step_script([_Step(uses="astrolift/docker-build@v1")])

    assert "docker build" in script


def test_other_builtins_are_unaffected_in_a_cluster_job():
    """git-checkout, kubectl-apply and astrolift-deploy all work
    unprivileged, which is why the builder image ships them."""
    for uses in ("astrolift/git-checkout@v1", "astrolift/kubectl-apply@v1"):
        script = render_step_script([_Step(uses=uses)], context=CLUSTER)
        assert script.strip()


def test_a_plain_run_step_is_unaffected():
    script = render_step_script([_Step(run="make test")], context=CLUSTER)

    assert "make test" in script


def test_a_run_step_that_merely_mentions_docker_is_not_refused():
    """The refusal is on the declared action, not on grep. An operator's own
    `docker --version` in a run step is their business, and guessing at shell
    intent would refuse scripts that never build anything."""
    script = render_step_script([_Step(run="echo docker build is not run here")], context=CLUSTER)

    assert "docker build" in script


def test_the_spawner_opts_in():
    """The wiring, pinned: without the flag the refusal never fires and the
    fix is inert."""
    import inspect

    from astrolift_workflows.activities import pipeline_job_spawn

    src = inspect.getsource(pipeline_job_spawn._spawn_pipeline_job_sync)
    assert "cluster_run" in src
