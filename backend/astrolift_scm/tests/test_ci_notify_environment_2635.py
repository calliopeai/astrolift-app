"""The GitHub notify step names the environment it deploys to (#2635).

A managed workflow posted no ``environment``, and the endpoint defaulted one
only while the app had a single live environment. A pull request preview is
a live environment too, and stays one after teardown, so every managed
deploy of a previewed app was refused with ``environment is required``.

These run the rendered step's own shell with a ``curl`` stub that records
what it posts.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
import yaml

from astrolift_clusters.models import ProviderPlugin, TenantCluster
from astrolift_identity.models import Organization, Team
from astrolift_lifecycle.models import AppEnvironment, PreviewEnvironment
from astrolift_registry.models import RegisteredApp
from astrolift_scm.services.workflow_sync import render_astrolift_ci_workflow

pytestmark = pytest.mark.django_db


@pytest.fixture
def cluster():
    org = Organization.objects.create(name="Acme", slug="acme-2635")
    plugin = ProviderPlugin(
        name="P",
        slug="p-2635",
        plugin_version="0.0.1",
        capabilities_manifest={},
        config_schema={},
    )
    ProviderPlugin.objects.bulk_create([plugin])
    return TenantCluster.objects.create(
        organization=org,
        name="c",
        slug="c-2635",
        provider_plugin=ProviderPlugin.objects.get(slug="p-2635"),
        provider_config={},
        endpoint="https://c.invalid",
        auth_method="kubeconfig",
        auth_config={},
    )


@pytest.fixture
def app(settings, cluster):
    settings.PLATFORM_API_URL = "https://platform.example.test"
    org = cluster.organization
    return RegisteredApp.objects.create(
        organization=org,
        team=Team.objects.create(organization=org, name="Eng", slug="eng-2635"),
        name="Studio",
        slug="studio",
        source_kind="github",
        source_repo="acme/studio",
        build_mode=RegisteredApp.BuildMode.NONE,
    )


def _environment(app, cluster, name):
    return AppEnvironment.objects.create(registered_app=app, tenant_cluster=cluster, name=name)


def _torn_down_preview(app, cluster, previewed):
    env = AppEnvironment.objects.create(
        registered_app=app,
        tenant_cluster=cluster,
        name="preview-pr-15",
        k8s_namespace="acme-studio-pr-15",
        previewed_environment=previewed,
    )
    PreviewEnvironment.objects.create(
        registered_app=app,
        pr_number=15,
        branch="feature/x",
        hostname="pr-15-studio.example.test",
        namespace="acme-studio-pr-15",
        app_environment=env,
        status=PreviewEnvironment.Status.TORN_DOWN,
    )


def _notify_step(app) -> dict:
    steps = yaml.safe_load(render_astrolift_ci_workflow(app))["jobs"]["build-and-deploy"]["steps"]
    return next(step for step in steps if step["name"] == "Notify Astrolift")


def _posted_body(app, tmp_path) -> dict:
    """Run the notify step with the env GitHub would give it; return what it posts."""
    notify = _notify_step(app)
    calls = tmp_path / "calls.jsonl"
    curl = tmp_path / "curl"
    curl.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        "with open(os.environ['CI_TEST_CALLS'], 'a') as stream:\n"
        "    stream.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "pathlib.Path(sys.argv[sys.argv.index('-o') + 1]).write_text('{}')\n"
        "print('201', end='')\n"
    )
    curl.chmod(0o700)
    command = notify["run"].replace("${{ github.sha }}", "a" * 40).replace("${{ github.ref_name }}", "main")
    command = command.replace("/tmp/astrolift-deploy-response.json", str(tmp_path / "response.json"))
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", command],
        env={
            **os.environ,
            **notify["env"],
            "TOKEN": "alft_dt_test",
            "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
            "CI_TEST_CALLS": str(calls),
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    argv = json.loads(calls.read_text().splitlines()[0])
    return json.loads(argv[argv.index("-d") + 1])


def test_notify_names_the_only_environment_that_is_not_a_preview(app, cluster, tmp_path):
    production = _environment(app, cluster, "production")
    _torn_down_preview(app, cluster, production)

    assert _notify_step(app)["env"]["ENVIRONMENT"] == "production"
    assert _posted_body(app, tmp_path)["environment"] == "production"


def test_notify_leaves_the_choice_to_the_endpoint_with_several_environments(app, cluster, tmp_path):
    _environment(app, cluster, "production")
    _environment(app, cluster, "staging")

    assert _notify_step(app)["env"]["ENVIRONMENT"] == ""
    assert _posted_body(app, tmp_path)["environment"] == ""


def test_notify_posts_any_environment_name_as_valid_json(app, cluster, tmp_path):
    _environment(app, cluster, 'pro"d\\uction')

    assert _posted_body(app, tmp_path)["environment"] == 'pro"d\\uction'
