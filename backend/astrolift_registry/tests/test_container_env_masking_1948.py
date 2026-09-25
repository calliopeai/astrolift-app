"""#1948: container, job and task env values need the ``revealAppSecret`` gate.

#1920/#1944 masked the app-wide ``[env]`` table on every read path. The
env of each ``[[workloads.containers]]`` entry, and of the one container a
``[[jobs]]``/``[[tasks]]`` entry desugars to, stayed readable with
``app.read``: in the manifest text, on ``AstroliftContainer.env``, and in
the rendered-manifest previews. ``astroliftSourceFile`` returned the
repo's ``astrolift.toml`` verbatim to any ``scm.read`` holder.

Each route is checked for an ``app.read``-only caller (keys kept, values
masked) and for a caller who holds ``secret.read`` and is elevated
(values shown). Grants are real RoleBinding rows: the reveal check reads
them directly, so a stubbed resolver would be invisible to it.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from astrolift_graphql import GUID
from astrolift_identity.models import Organization, Project, Role, RoleBinding, Team
from astrolift_identity.session_elevation import METHOD_PASSWORD, elevate
from astrolift_lifecycle.models import AppEnvironment, Deployment
from astrolift_manifest.env_edit import REDACTED_ENV_VALUE
from astrolift_manifest.normalize import NormalizationDefaults, normalize
from astrolift_manifest.parser import parse_raw
from astrolift_manifest.persist import persist_manifest
from astrolift_registry.models import Container, RegisteredApp
from astrolift_registry.schema.mutations import RegistryMutation
from astrolift_registry.schema.mutations.types import UpdateManifestInput
from astrolift_registry.schema.queries import RegistryQuery
from astrolift_scm.models import SourceConnection
from astrolift_scm.schema.queries import ScmQuery
from core.tenancy import TenantContext, tenant_context

pytestmark = pytest.mark.django_db

_V = REDACTED_ENV_VALUE
_WEB_KEY = "sk-live-web-1948"
_OLD_WEB_KEY = "sk-live-old-1948"
_JOB_PASSWORD = "pw-job-1948"
_TASK_TOKEN = "tok-task-1948"
_SECRETS = (_WEB_KEY, _OLD_WEB_KEY, _JOB_PASSWORD, _TASK_TOKEN)

MANIFEST = f"""\
name = "hello"

[[workloads]]
name = "web"
kind = "deployment"

  [[workloads.containers]]
  name = "app"
  is_primary = true
  port = 8080

    [workloads.containers.env]
    # old value {_OLD_WEB_KEY}
    API_KEY = "{_WEB_KEY}"
    LOG_LEVEL = "info"

[[jobs]]
name = "nightly"
schedule = "0 3 * * *"
env = {{ DB_PASSWORD = "{_JOB_PASSWORD}" }}

[[tasks]]
name = "migrate"

[tasks.env]
TOKEN = "{_TASK_TOKEN}"
"""


class _FakeSession(dict):
    modified = False


def _info(user, *, session: dict | None = None):
    request = SimpleNamespace(user=user, session=session if session is not None else _FakeSession(), META={})
    return SimpleNamespace(context=SimpleNamespace(request=request, user=user))


def _elevated_session() -> _FakeSession:
    """Elevated whether or not the install requires step-up, so a revealer
    test holds even while a concurrent run has the flag on."""
    session = _FakeSession()
    elevate(session, method=METHOD_PASSWORD, ttl_seconds=300)
    return session


def _user(username: str):
    user, _ = get_user_model().objects.get_or_create(
        username=username, defaults={"email": f"{username}@example.com"}
    )
    return user


def _grant(user, *permissions: str, scope_kind: str, scope_id: int) -> None:
    slug = f"role-{scope_kind.lower()}-{scope_id}-{'-'.join(permissions)}-{user.pk}"
    role = Role.objects.create(name=slug, slug=slug, permissions=list(permissions))
    RoleBinding.objects.create(user=user, role=role, scope_kind=scope_kind, scope_id=scope_id)


def _scaffold(seed_cluster, *, org_slug: str = "acme-1948"):
    org = Organization.objects.create(name="Acme", slug=org_slug)
    team = Team.objects.create(organization=org, name="Eng", slug=f"eng-{org_slug}")
    project = Project.objects.create(organization=org, team=team, name="Demo", slug=f"demo-{org_slug}")
    cluster = seed_cluster(org, slug=org_slug)
    app = _app(org, team, project, slug="hello")
    AppEnvironment.objects.create(registered_app=app, tenant_cluster=cluster, name="prod")
    return org, app


def _app(org, team, project, *, slug: str) -> RegisteredApp:
    app = RegisteredApp.objects.create(
        organization=org, team=team, project=project, name=slug, slug=slug, manifest_raw=MANIFEST
    )
    persist_manifest(app, normalize(parse_raw(MANIFEST), defaults=NormalizationDefaults()), raw_text=MANIFEST)
    return app


def _ctx(org, user):
    return tenant_context(TenantContext(organization_id=org.id, actor_user_id=user.id))


def _assert_no_secret(value) -> None:
    text = value if isinstance(value, str) else json.dumps(value)
    for secret in _SECRETS:
        assert secret not in text


def _container_env(resources: list[dict], kind: str) -> dict[str, str]:
    resource = next(r for r in resources if r["kind"] == kind)
    spec = resource["spec"]
    if kind == "CronJob":
        spec = spec["jobTemplate"]["spec"]
    return {entry["name"]: entry["value"] for entry in spec["template"]["spec"]["containers"][0]["env"]}


# ---- manifest text ---------------------------------------------------------


def test_raw_manifest_masks_container_job_and_task_env_for_app_read_only(seed_cluster):
    org, app = _scaffold(seed_cluster)
    viewer = _user("text-app-read")
    _grant(viewer, "app.read", scope_kind="ORG", scope_id=org.id)

    with _ctx(org, viewer):
        result = RegistryQuery().astrolift_app(_info(viewer), slug=app.slug)

    _assert_no_secret(result.raw_manifest)
    for key in ("API_KEY", "LOG_LEVEL", "DB_PASSWORD", "TOKEN"):
        assert key in result.raw_manifest
    assert f'API_KEY = "{_V}"' in result.raw_manifest


def test_raw_manifest_reveals_container_env_for_secret_read_elevated(seed_cluster):
    org, app = _scaffold(seed_cluster)
    viewer = _user("text-revealer")
    _grant(viewer, "app.read", "secret.read", scope_kind="ORG", scope_id=org.id)

    with _ctx(org, viewer):
        result = RegistryQuery().astrolift_app(_info(viewer, session=_elevated_session()), slug=app.slug)

    assert result.raw_manifest == MANIFEST


def _masked_read(org, app, caller) -> str:
    with _ctx(org, caller):
        read = RegistryQuery().astrolift_app(_info(caller), slug=app.slug)
    _assert_no_secret(read.raw_manifest)
    return read.raw_manifest


def _save(org, app, caller, text: str):
    with _ctx(org, caller):
        return RegistryMutation().update_manifest(
            _info(caller), input=UpdateManifestInput(id=GUID(str(app.guid)), raw_manifest=text)
        )


def test_update_manifest_masked_round_trip_keeps_container_env_byte_identical(seed_cluster):
    """Load the masked editor text, change something unrelated, save the
    whole document: every container, job and task value is put back."""
    org, app = _scaffold(seed_cluster)
    editor = _user("text-round-trip")
    _grant(editor, "app.read", "app.update", scope_kind="ORG", scope_id=org.id)

    masked = _masked_read(org, app, editor)
    result = _save(org, app, editor, masked.replace('schedule = "0 3 * * *"', 'schedule = "0 4 * * *"'))

    assert result.ok is True, result.errors
    app.refresh_from_db()
    assert app.manifest_raw_staged == MANIFEST.replace('schedule = "0 3 * * *"', 'schedule = "0 4 * * *"')
    _assert_no_secret(result.data.raw_manifest_staged)


def test_update_manifest_refuses_a_container_placeholder_with_no_stored_value(seed_cluster):
    org, app = _scaffold(seed_cluster)
    editor = _user("text-ghost")
    _grant(editor, "app.read", "app.update", scope_kind="ORG", scope_id=org.id)

    masked = _masked_read(org, app, editor)
    result = _save(org, app, editor, masked.replace('LOG_LEVEL = "', f'GHOST = "{_V}"\n    LOG_LEVEL = "'))

    assert result.ok is False
    assert result.errors[0].code == "VALIDATION"
    assert result.errors[0].field == "rawManifest"
    assert "workloads.web.containers.app.env.GHOST" in result.errors[0].message
    app.refresh_from_db()
    assert app.manifest_raw_staged == ""


def test_register_app_refuses_a_masked_container_env_placeholder(permission_resolver, seed_cluster):
    """A new app has no stored value to put back."""
    from astrolift_registry.schema.mutations import RegisterAppInput
    from core.permissions import Permission

    org, app = _scaffold(seed_cluster)
    permission_resolver.grant(Permission.APP_CREATE)

    with tenant_context(TenantContext(organization_id=org.id)):
        result = RegistryMutation().register_app(
            SimpleNamespace(context=SimpleNamespace(user=None, request=None)),
            input=RegisterAppInput(
                project_id=str(app.project.guid),
                name="Copied",
                slug="copied-1948",
                source_repo="acme/copied-1948",
                manifest_raw=MANIFEST.replace(_TASK_TOKEN, _V),
            ),
        )

    assert result.ok is False
    assert result.errors[0].field == "manifestRaw"
    assert "tasks.migrate.env.TOKEN" in result.errors[0].message
    assert not RegisteredApp.objects.filter(slug="copied-1948").exists()


def test_mutation_audit_log_masks_container_env_in_a_manifest_variable():
    from graphql import parse

    from config.schema import schema
    from core.schema.audit import redact_operation_variables

    redacted, sensitive = redact_operation_variables(
        {"m": MANIFEST},
        document=parse(
            "mutation($m: String!) { updateManifest(input: {id: "
            '"00000000-0000-0000-0000-000000000001", rawManifest: $m}) { ok } }'
        ),
        schema=schema._schema,
    )

    assert sensitive is True
    _assert_no_secret(redacted["m"])
    assert "API_KEY" in redacted["m"]


# ---- astroliftContainers ---------------------------------------------------


def _containers(org, viewer, *, session=None):
    with _ctx(org, viewer):
        rows = RegistryQuery().astrolift_containers(_info(viewer, session=session))
    return {(row.workload_slug, row.name): row.env for row in rows}


def test_containers_mask_env_values_for_app_read_only(seed_cluster):
    org, _ = _scaffold(seed_cluster)
    viewer = _user("containers-app-read")
    _grant(viewer, "app.read", scope_kind="ORG", scope_id=org.id)

    env = _containers(org, viewer)

    assert env[("web", "app")] == {"API_KEY": _V, "LOG_LEVEL": _V}
    assert env[("nightly", "nightly")] == {"DB_PASSWORD": _V}
    assert env[("migrate", "migrate")] == {"TOKEN": _V}


def test_containers_reveal_env_values_for_secret_read_elevated(seed_cluster):
    org, _ = _scaffold(seed_cluster)
    viewer = _user("containers-revealer")
    _grant(viewer, "app.read", "secret.read", scope_kind="ORG", scope_id=org.id)

    env = _containers(org, viewer, session=_elevated_session())

    assert env[("web", "app")] == {"API_KEY": _WEB_KEY, "LOG_LEVEL": "info"}
    assert env[("nightly", "nightly")] == {"DB_PASSWORD": _JOB_PASSWORD}


def test_containers_gate_each_app_on_its_own_grants(seed_cluster):
    """One page spans two apps; ``secret.read`` on one of them reveals
    that app's containers only."""
    org, app = _scaffold(seed_cluster)
    _app(org, app.team, app.project, slug="other")
    viewer = _user("containers-per-app")
    _grant(viewer, "app.read", scope_kind="ORG", scope_id=org.id)
    _grant(viewer, "secret.read", scope_kind="APP", scope_id=app.id)

    with _ctx(org, viewer):
        rows = RegistryQuery().astrolift_containers(_info(viewer, session=_elevated_session()))

    revealed_app_container = str(Container.objects.get(workload__registered_app=app, name="app").guid)
    web_env = {str(row.id): row.env for row in rows if row.name == "app"}
    assert len(web_env) == 2
    assert web_env.pop(revealed_app_container) == {"API_KEY": _WEB_KEY, "LOG_LEVEL": "info"}
    assert list(web_env.values()) == [{"API_KEY": _V, "LOG_LEVEL": _V}]


# ---- rendered-manifest previews -------------------------------------------


def test_rendered_manifest_masks_container_env_for_app_read_only(seed_cluster):
    """User-set values are masked; the env the platform injects while
    rendering (the image tag) still renders."""
    org, app = _scaffold(seed_cluster)
    viewer = _user("render-app-read")
    _grant(viewer, "app.read", scope_kind="ORG", scope_id=org.id)

    with _ctx(org, viewer):
        result = RegistryQuery().astrolift_rendered_manifest(_info(viewer), app_slug=app.slug, image_tag="v2")

    assert result.error is None
    _assert_no_secret(result.resources)
    web = _container_env(result.resources, "Deployment")
    assert web["API_KEY"] == _V
    assert web["LOG_LEVEL"] == _V
    assert web["ASTROLIFT_COMMIT"] == "v2"
    assert _container_env(result.resources, "CronJob")["DB_PASSWORD"] == _V
    assert _container_env(result.resources, "Job")["TOKEN"] == _V


def test_rendered_manifest_reveals_container_env_for_secret_read_elevated(seed_cluster):
    org, app = _scaffold(seed_cluster)
    viewer = _user("render-revealer")
    _grant(viewer, "app.read", "secret.read", scope_kind="ORG", scope_id=org.id)

    with _ctx(org, viewer):
        result = RegistryQuery().astrolift_rendered_manifest(
            _info(viewer, session=_elevated_session()), app_slug=app.slug
        )

    assert _container_env(result.resources, "Deployment")["API_KEY"] == _WEB_KEY
    assert _container_env(result.resources, "CronJob")["DB_PASSWORD"] == _JOB_PASSWORD


def test_workload_manifest_masks_current_and_previous_resources(seed_cluster):
    org, app = _scaffold(seed_cluster)
    Deployment.objects.create(
        registered_app=app,
        app_environment=AppEnvironment.objects.get(registered_app=app),
        trigger_kind=Deployment.TriggerKind.MANUAL.value,
        status=Deployment.Status.RUNNING.value,
        image_tag="v1",
    )
    viewer = _user("workload-render-app-read")
    _grant(viewer, "app.read", scope_kind="ORG", scope_id=org.id)

    with _ctx(org, viewer):
        result = RegistryQuery().astrolift_workload_manifest(
            _info(viewer), app_slug=app.slug, workload_slug="web", image_tag="v2"
        )

    assert result.previous_image_tag == "v1"
    assert result.resources_previous
    _assert_no_secret(result.resources)
    _assert_no_secret(result.resources_previous)
    assert _container_env(result.resources, "Deployment")["API_KEY"] == _V
    assert _container_env(result.resources_previous, "Deployment")["API_KEY"] == _V


# ---- astroliftSourceFile ---------------------------------------------------


def _connection(org) -> SourceConnection:
    return SourceConnection.objects.create(
        organization=org,
        kind=SourceConnection.Kind.GITHUB_OAUTH_APP,
        display_name="Acme GitHub",
        oauth_client_id="cid",
        app_client_id="cid",
        is_active=True,
    )


def _source_file(monkeypatch, org, viewer, *, path: str, content: str, session=None) -> str | None:
    """The SCM host is the one thing faked: this tests what the resolver
    does with a file, not GitHub."""
    monkeypatch.setattr("astrolift_scm.schema.queries.fetch_file", lambda *args, **kwargs: content)
    connection = _connection(org)
    with _ctx(org, viewer):
        result = ScmQuery().astrolift_source_file(
            _info(viewer, session=session),
            connection_id=str(connection.guid),
            repo_full_name="acme/hello",
            path=path,
            ref="main",
        )
    assert result.error_code is None
    return result.content


def test_source_file_masks_a_repo_manifest_for_scm_read_only(monkeypatch, seed_cluster):
    """Push to Repo writes the staged env literals into the repo."""
    org, _ = _scaffold(seed_cluster)
    viewer = _user("scm-read-only")
    _grant(viewer, "scm.read", "app.read", scope_kind="ORG", scope_id=org.id)

    content = _source_file(monkeypatch, org, viewer, path="services/api/astrolift.toml", content=MANIFEST)

    _assert_no_secret(content)
    assert f'API_KEY = "{_V}"' in content
    assert f'TOKEN = "{_V}"' in content


def test_source_file_reveals_a_repo_manifest_for_org_wide_secret_read(monkeypatch, seed_cluster):
    org, _ = _scaffold(seed_cluster)
    viewer = _user("scm-revealer")
    _grant(viewer, "scm.read", "app.read", "secret.read", scope_kind="ORG", scope_id=org.id)

    content = _source_file(
        monkeypatch, org, viewer, path="astrolift.toml", content=MANIFEST, session=_elevated_session()
    )

    assert content == MANIFEST


def test_source_file_needs_org_wide_secret_read_to_reveal(monkeypatch, seed_cluster):
    """The file has no app to scope to; ``secret.read`` on one app is not
    a grant over whatever manifest a repo path holds."""
    org, app = _scaffold(seed_cluster)
    viewer = _user("scm-app-scoped-secret")
    _grant(viewer, "scm.read", "app.read", scope_kind="ORG", scope_id=org.id)
    _grant(viewer, "secret.read", scope_kind="APP", scope_id=app.id)

    content = _source_file(
        monkeypatch, org, viewer, path="astrolift.toml", content=MANIFEST, session=_elevated_session()
    )

    _assert_no_secret(content)


def test_source_file_returns_other_files_as_fetched(monkeypatch, seed_cluster):
    org, _ = _scaffold(seed_cluster)
    viewer = _user("scm-readme")
    _grant(viewer, "scm.read", scope_kind="ORG", scope_id=org.id)
    readme = "# Hello\n\nSet `env` in astrolift.toml: see the docs.\n"

    assert _source_file(monkeypatch, org, viewer, path="README.md", content=readme) == readme
