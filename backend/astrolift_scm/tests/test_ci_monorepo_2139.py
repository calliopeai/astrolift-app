"""Persisted apps sharing a repository keep distinct CI targets and build inputs."""

from __future__ import annotations

import json
import os
import posixpath
import subprocess
import sys

import pytest
import yaml

from astrolift_identity.models import Organization, Team
from astrolift_registry.models import RegisteredApp
from astrolift_scm.ci_identity import github_ci_secret_name
from astrolift_scm.services.workflow_sync import (
    github_workflow_path_for,
    render_astrolift_ci_workflow,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def apps(settings):
    settings.PLATFORM_API_URL = "https://platform.example.test"
    org = Organization.objects.create(name="Shared repository", slug="shared-ci-2139")
    team = Team.objects.create(organization=org, name="App team", slug="app-team")
    return tuple(
        RegisteredApp.objects.create(
            organization=org,
            team=team,
            name=slug,
            slug=slug,
            source_kind="github",
            source_repo="example/monorepo",
            manifest_path=manifest,
            dockerfile_path=dockerfile,
            build_context=context,
            registry_repo_uri=f"123456789012.dkr.ecr.us-west-2.amazonaws.com/{slug}",
            push_role_ref="arn:aws:iam::123456789012:role/ci-build",
        )
        for slug, manifest, dockerfile, context in (
            ("portal", "astrolift.toml", "Dockerfile.portal", "."),
            (
                "demo",
                "extensions/demo/astrolift.toml",
                "Dockerfile",
                "extensions/demo",
            ),
        )
    )


def test_two_persisted_app_manifests_have_independent_stable_workflow_files(apps):
    portal, demo = apps
    first = github_workflow_path_for(portal)
    assert first != github_workflow_path_for(demo)
    portal.slug = "renamed-portal"
    portal.save(update_fields=["slug"])
    assert github_workflow_path_for(portal) == first


def test_each_generated_build_uses_its_persisted_dockerfile_context_and_image(apps):
    for app in apps:
        document = yaml.safe_load(render_astrolift_ci_workflow(app))
        steps = document["jobs"]["build-and-deploy"]["steps"]
        build = next(step for step in steps if step["name"] == "Build and push image")
        assert build["env"]["DOCKERFILE"] == posixpath.normpath(
            posixpath.join(app.build_context, app.dockerfile_path)
        )
        assert build["env"]["BUILD_CONTEXT"] == app.build_context
        assert build["env"]["IMAGE"] == app.registry_repo_uri + ":${{ github.sha }}"
        assert 'os.environ["DOCKERFILE"]' in build["run"]
        assert 'os.environ["BUILD_CONTEXT"]' in build["run"]
        assert "subprocess.run(command, check=True)" in build["run"]
        assert 'docker push "$IMAGE"' in build["run"]
        notify = next(step for step in steps if step["name"] == "Notify Astrolift")
        assert notify["env"]["APP_SLUG"] == app.slug
        assert "if" not in notify


def test_ci_pushed_app_with_missing_build_metadata_refuses_notify_only_workflow(apps):
    app = apps[0]
    app.registry_repo_uri = ""
    app.save(update_fields=["registry_repo_uri"])
    with pytest.raises(ValueError, match="registry"):
        render_astrolift_ci_workflow(app)


@pytest.mark.parametrize("field", ["dockerfile_path", "build_context"])
def test_ci_pushed_app_with_blank_source_build_input_is_actionable(apps, field):
    app = apps[0]
    setattr(app, field, "")
    app.save(update_fields=[field])
    with pytest.raises(ValueError, match="Dockerfile|context"):
        render_astrolift_ci_workflow(app)


def test_each_app_references_its_own_deploy_secret_after_a_sibling_is_registered(apps):
    first, second = apps
    names = [github_ci_secret_name(app, "ASTROLIFT_DEPLOY_TOKEN") for app in apps]
    assert names[0] != names[1]
    for app, name in zip((first, second), names, strict=True):
        document = yaml.safe_load(render_astrolift_ci_workflow(app))
        notify = document["jobs"]["build-and-deploy"]["steps"][-1]
        assert notify["env"]["TOKEN"] == "${{ secrets['" + name + "'] }}"


@pytest.mark.parametrize("app_index", [0, 1])
@pytest.mark.parametrize("failure", ["", "build", "push"])
def test_generated_shell_preserves_app_build_inputs_and_stops_before_notify_on_failure(
    apps, app_index, failure, tmp_path
):
    """Execute generated shell with recording CLI shims, never the Docker daemon."""
    app = apps[app_index]
    app.build_args = {"LABEL": 'spaces "quotes" $(touch should-not-exist)'}
    app.save(update_fields=["build_args"])
    sha = "a" * 40
    document = yaml.safe_load(render_astrolift_ci_workflow(app))
    steps = document["jobs"]["build-and-deploy"]["steps"]
    build = next(step for step in steps if step["name"] == "Build and push image")
    notify = next(step for step in steps if step["name"] == "Notify Astrolift")
    calls_path = tmp_path / "calls.jsonl"
    script = (
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        "name = pathlib.Path(sys.argv[0]).name\n"
        "with open(os.environ['CI_TEST_CALLS'], 'a') as stream:\n"
        " stream.write(json.dumps([name, *sys.argv[1:]]) + '\\n')\n"
        "if name == 'docker' and sys.argv[1] == os.environ['CI_TEST_FAILURE']:\n"
        " sys.exit(23)\n"
        "if name == 'curl':\n"
        " pathlib.Path(sys.argv[sys.argv.index('-o') + 1]).write_text('{}')\n"
        " print('200', end='')\n"
    )
    for command in ("docker", "curl"):
        path = tmp_path / command
        path.write_text(script)
        path.chmod(0o700)
    env = dict(
        os.environ,
        PATH=str(tmp_path) + os.pathsep + os.environ["PATH"],
        CI_TEST_CALLS=str(calls_path),
        CI_TEST_FAILURE=failure,
    )
    outcomes = []
    for step in (build, notify):
        step_env = dict(
            env, **{key: value.replace("${{ github.sha }}", sha) for key, value in step["env"].items()}
        )
        step_env["TOKEN"] = "unit-test-token"
        command = step["run"].replace("${{ github.sha }}", sha).replace("${{ github.ref_name }}", "main")
        command = command.replace("/tmp/astrolift-deploy-response.json", str(tmp_path / "response.json"))
        outcome = subprocess.run(
            ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", command],
            cwd=tmp_path,
            env=step_env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        outcomes.append(outcome.returncode)
        if outcome.returncode:
            break
    calls = [json.loads(line) for line in calls_path.read_text().splitlines()]
    assert calls[0] == [
        "docker",
        "build",
        "-f",
        posixpath.normpath(posixpath.join(app.build_context, app.dockerfile_path)),
        "-t",
        app.registry_repo_uri + ":" + sha,
        "--build-arg",
        "LABEL=" + app.build_args["LABEL"],
        app.build_context,
    ]
    assert not (tmp_path / "should-not-exist").exists()
    if failure:
        assert outcomes[-1] != 0
        assert not any(call[0] == "curl" for call in calls)
        assert len(calls) == (1 if failure == "build" else 2)
    else:
        assert outcomes == [0, 0]
        assert calls[1] == ["docker", "push", app.registry_repo_uri + ":" + sha]
        notify_call = calls[2]
        assert notify_call[0] == "curl"
        assert "https://platform.example.test/api/cli/v1/apps/" + app.slug + "/deploy/" in notify_call
        body = json.loads(notify_call[notify_call.index("-d") + 1])
        assert body == {"image_tags": {"*": sha}, "commit_sha": sha, "branch": "main", "trigger_kind": "ci"}


@pytest.mark.parametrize(
    "field,value",
    [
        ("build_context", "/tmp/outside"),
        ("build_context", "../../outside"),
        ("build_context", "--host-option"),
        ("dockerfile_path", "../Dockerfile"),
        ("dockerfile_path", "/tmp/Dockerfile"),
        ("dockerfile_path", "."),
        ("dockerfile_path", "bad\\path"),
        ("dockerfile_path", "bad\x00path"),
    ],
)
def test_unsafe_saved_build_paths_refuse_before_generating_ci(apps, field, value):
    setattr(apps[0], field, value)
    with pytest.raises(ValueError, match="safe repository"):
        render_astrolift_ci_workflow(apps[0])


@pytest.mark.parametrize("value", [[], False, {"LABEL": []}, {"invalid-name": "value"}])
def test_saved_build_arguments_require_an_actual_string_map(apps, value):
    apps[0].build_args = value
    with pytest.raises(ValueError, match="build arguments"):
        render_astrolift_ci_workflow(apps[0])


# --- #2300 ---------------------------------------------------------------


def test_deploy_token_lookup_is_a_single_quoted_expression_literal(apps):
    """Actions expressions only accept single-quoted literals; v9 rendered
    ``secrets["..."]`` and every run failed at parse time (#2300)."""
    body = render_astrolift_ci_workflow(apps[0])
    name = github_ci_secret_name(apps[0], "ASTROLIFT_DEPLOY_TOKEN")
    assert "TOKEN: ${{ secrets['" + name + "'] }}" in body
    assert 'secrets["' not in body


def test_ci_pushed_build_reads_the_manifest_primary_container_paths(apps):
    """An app left at the Dockerfile/"." defaults builds what its manifest's
    primary container declares, as the platform build does (#2300)."""
    from astrolift_registry.models import Workload

    app = apps[0]
    app.dockerfile_path, app.build_context = "Dockerfile", "."
    app.save(update_fields=["dockerfile_path", "build_context"])
    workload = Workload.objects.create(registered_app=app, name="web", slug="web", kind="deployment")
    workload.containers.create(
        name="web", is_primary=True, dockerfile_path="extensions/portal-container/Dockerfile"
    )

    steps = yaml.safe_load(render_astrolift_ci_workflow(app))["jobs"]["build-and-deploy"]["steps"]
    build = next(step for step in steps if step["name"] == "Build and push image")
    assert build["env"]["DOCKERFILE"] == "extensions/portal-container/Dockerfile"
    assert build["env"]["BUILD_CONTEXT"] == "."


def test_ci_pushed_build_keeps_an_explicit_app_level_dockerfile(apps):
    """An app-level path set on purpose still wins over the manifest."""
    from astrolift_registry.models import Workload

    app = apps[0]  # dockerfile_path="Dockerfile.portal"
    workload = Workload.objects.create(registered_app=app, name="web", slug="web", kind="deployment")
    workload.containers.create(name="web", is_primary=True, dockerfile_path="other/Dockerfile")

    steps = yaml.safe_load(render_astrolift_ci_workflow(app))["jobs"]["build-and-deploy"]["steps"]
    build = next(step for step in steps if step["name"] == "Build and push image")
    assert build["env"]["DOCKERFILE"] == "Dockerfile.portal"


def test_ci_pushed_build_refuses_a_container_path_outside_the_repo(apps):
    from astrolift_registry.models import Workload

    app = apps[0]
    app.dockerfile_path = "Dockerfile"
    app.save(update_fields=["dockerfile_path"])
    workload = Workload.objects.create(registered_app=app, name="web", slug="web", kind="deployment")
    workload.containers.create(name="web", is_primary=True, build_context="../..")

    with pytest.raises(ValueError, match="escapes the repository root"):
        render_astrolift_ci_workflow(app)
