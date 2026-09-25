"""#1948: container, job and task ``env`` literals are masked like ``[env]``.

#1920/#1944 masked the app-wide ``[env]`` table only. A container's
``env`` (``[[workloads.containers]]``, and the one container a
``[[jobs]]`` or ``[[tasks]]`` entry desugars to) is rendered straight
into the pod spec, and holds secrets as often. These cover the text
side: the in-place mask, the restore a masked save relies on, and the
parser's refusal of a placeholder nobody put back.
"""

from __future__ import annotations

import tomllib

import pytest

from astrolift_manifest.env_edit import (
    REDACTED_DOCUMENT,
    REDACTED_ENV_VALUE,
    redact_env_values,
    resolve_masked_env_values,
)
from astrolift_manifest.parser import ManifestError, parse_raw

_V = REDACTED_ENV_VALUE

_WEB_KEY = "sk-live-web-111"
_SIDECAR_KEY = "sk-live-sidecar-222"
_JOB_PASSWORD = "pw-job-333"
_TASK_TOKEN = "tok-task-444"
_OLD_WEB_KEY = "sk-live-old-555"
_APP_KEY = "sk-live-app-666"
_SECRETS = (_WEB_KEY, _SIDECAR_KEY, _JOB_PASSWORD, _TASK_TOKEN, _OLD_WEB_KEY, _APP_KEY)


def _marker(ordinal: int) -> str:
    return f"# [ASTROLIFT_REDACTED_ENV_COMMENT:{ordinal}]"


# Every form a container, job or task env takes: a header table with a
# comment and a trailing comment, an inline table, a dotted key, and a
# multi-line string. A valid manifest, so parse_raw accepts it.
MANIFEST = f'''\
# astrolift.toml
name = "hello"

[env]
APP_KEY = "{_APP_KEY}"

[[workloads]]
name = "web"
kind = "deployment"

  [[workloads.containers]]
  name = "app"
  is_primary = true
  port = 8080

    [workloads.containers.env]
    # rotated; old value was {_OLD_WEB_KEY}
    API_KEY   = "{_WEB_KEY}"   # prod
    LOG_LEVEL = 'info'
    WORKERS = 4

  [[workloads.containers]]
  name = "sidecar"
  env = {{ API_KEY = "{_SIDECAR_KEY}", MODE = "proxy" }}

[[jobs]]
name = "nightly"
schedule = "0 3 * * *"
env.DB_PASSWORD = "{_JOB_PASSWORD}"

[[tasks]]
name = "migrate"

[tasks.env]
TOKEN = """{_TASK_TOKEN}"""
'''

MANIFEST_MASKED = f"""\
# astrolift.toml
name = "hello"

[env]
APP_KEY = "{_V}"

[[workloads]]
name = "web"
kind = "deployment"

  [[workloads.containers]]
  name = "app"
  is_primary = true
  port = 8080

    [workloads.containers.env]
    {_marker(1)}
    API_KEY   = "{_V}"   {_marker(2)}
    LOG_LEVEL = "{_V}"
    WORKERS = "{_V}"

  [[workloads.containers]]
  name = "sidecar"
  env = {{ API_KEY = "{_V}", MODE = "{_V}" }}

[[jobs]]
name = "nightly"
schedule = "0 3 * * *"
env.DB_PASSWORD = "{_V}"

[[tasks]]
name = "migrate"

[tasks.env]
TOKEN = "{_V}"
"""


def test_the_fixture_is_a_valid_manifest() -> None:
    """The round-trip tests below mean something only if a save of this
    text would pass the parser."""
    raw = parse_raw(MANIFEST)
    containers = {c.name: dict(c.env) for w in raw.workloads for c in w.containers}
    assert containers["app"]["API_KEY"] == _WEB_KEY
    assert containers["sidecar"]["API_KEY"] == _SIDECAR_KEY
    assert containers["nightly"]["DB_PASSWORD"] == _JOB_PASSWORD
    assert containers["migrate"]["TOKEN"] == _TASK_TOKEN


# ---- redact_env_values ---------------------------------------------------


def test_redact_masks_container_job_and_task_env_in_place() -> None:
    """Every env value and every comment in an env section is masked;
    keys and the rest of the document are byte-identical."""
    assert redact_env_values(MANIFEST) == MANIFEST_MASKED


def test_redact_leaves_no_secret_in_any_env_form() -> None:
    masked = redact_env_values(MANIFEST)
    for secret in _SECRETS:
        assert secret not in masked
    data = tomllib.loads(masked)
    app, sidecar = data["workloads"][0]["containers"]
    assert app["env"] == {"API_KEY": _V, "LOG_LEVEL": _V, "WORKERS": _V}
    assert sidecar["env"] == {"API_KEY": _V, "MODE": _V}
    assert data["jobs"][0]["env"] == {"DB_PASSWORD": _V}
    assert data["tasks"][0]["env"] == {"TOKEN": _V}
    assert app["port"] == 8080


def test_redact_masks_an_inline_array_of_containers() -> None:
    """``containers = [{...}]`` is the shape ``tomli_w`` writes, so it is
    also what a masked rebuild comes back as."""
    text = (
        'name = "hello"\n\n[[workloads]]\nname = "web"\nkind = "deployment"\ncontainers = [\n'
        f'    {{ name = "app", env = {{ API_KEY = "{_WEB_KEY}" }} }},\n]\n'
    )
    masked = redact_env_values(text)
    assert masked == text.replace(_WEB_KEY, _V)
    assert resolve_masked_env_values(masked, fallback_text=text) == (text, [])


def test_redact_masks_a_container_env_that_is_not_a_table() -> None:
    text = (
        'name = "hello"\n\n[[workloads]]\nname = "web"\n\n'
        f'  [[workloads.containers]]\n  name = "app"\n  env = "API_KEY={_WEB_KEY}"\n'
    )
    masked = redact_env_values(text)
    assert _WEB_KEY not in masked
    assert tomllib.loads(masked)["workloads"][0]["containers"][0]["env"] == _V


def test_redact_falls_back_to_a_masked_rebuild_for_a_nested_container_env_table() -> None:
    text = (
        'name = "hello"\n\n[[workloads]]\nname = "web"\n\n  [[workloads.containers]]\n  name = "app"\n\n'
        f'    [workloads.containers.env]\n    API_KEY = "{_WEB_KEY}"\n\n'
        f'    [workloads.containers.env.NESTED]\n    INNER = "{_SIDECAR_KEY}"\n'
    )
    masked = redact_env_values(text)
    assert _WEB_KEY not in masked
    assert _SIDECAR_KEY not in masked
    assert tomllib.loads(masked)["workloads"][0]["containers"][0]["env"] == {"API_KEY": _V, "NESTED": _V}


def test_redact_leaves_env_named_keys_outside_env_positions_alone() -> None:
    """Only the tables the parser reads env from are masked: an ``env``
    key under a managed service's config is not a pod's environment."""
    text = (
        'name = "hello"\n\n[[managed_services]]\nkind = "postgres"\n\n'
        '  [managed_services.config]\n  env = "staging"\n'
    )
    assert redact_env_values(text) == text


# ---- resolve_masked_env_values ------------------------------------------


def test_masked_round_trip_restores_container_env_byte_for_byte() -> None:
    """Load masked, save unchanged: every container, job and task value,
    and the comments in their sections, come back exactly."""
    assert resolve_masked_env_values(MANIFEST_MASKED, fallback_text=MANIFEST) == (MANIFEST, [])


def test_restore_matches_tables_by_name_not_position() -> None:
    """A workload added ahead of ``web`` and the two containers swapped:
    each placeholder still gets its own container's value."""
    app_block = MANIFEST_MASKED[
        MANIFEST_MASKED.index('  [[workloads.containers]]\n  name = "app"') : MANIFEST_MASKED.index(
            '  [[workloads.containers]]\n  name = "sidecar"'
        )
    ]
    sidecar_block = MANIFEST_MASKED[
        MANIFEST_MASKED.index('  [[workloads.containers]]\n  name = "sidecar"') : MANIFEST_MASKED.index(
            "[[jobs]]"
        )
    ]
    edited = MANIFEST_MASKED.replace(app_block + sidecar_block, sidecar_block + app_block).replace(
        '[[workloads]]\nname = "web"',
        '[[workloads]]\nname = "api"\nkind = "deployment"\n\n'
        '  [[workloads.containers]]\n  name = "app"\n  env = { API_KEY = "typed-by-hand" }\n\n'
        '[[workloads]]\nname = "web"',
    )

    resolved, missing = resolve_masked_env_values(edited, fallback_text=MANIFEST)

    assert missing == []
    workloads = tomllib.loads(resolved)["workloads"]
    assert workloads[0]["containers"][0]["env"] == {"API_KEY": "typed-by-hand"}
    sidecar, app = workloads[1]["containers"]
    assert sidecar["env"] == {"API_KEY": _SIDECAR_KEY, "MODE": "proxy"}
    assert app["env"] == {"API_KEY": _WEB_KEY, "LOG_LEVEL": "info", "WORKERS": 4}


def test_restore_refuses_a_placeholder_another_table_holds_the_value_for() -> None:
    """``DB_PASSWORD`` is stored for the nightly job only. A placeholder
    under the sidecar must not pull the job's value into a container
    whose command the caller may control."""
    edited = MANIFEST_MASKED.replace(f'MODE = "{_V}" }}', f'MODE = "{_V}", DB_PASSWORD = "{_V}" }}')
    assert resolve_masked_env_values(edited, fallback_text=MANIFEST) == (
        None,
        ["workloads.web.containers.sidecar.env.DB_PASSWORD"],
    )


def test_restore_refuses_the_placeholders_of_a_renamed_container() -> None:
    edited = MANIFEST_MASKED.replace('name = "app"', 'name = "api"')
    resolved, missing = resolve_masked_env_values(edited, fallback_text=MANIFEST)
    assert resolved is None
    assert missing == [
        "workloads.web.containers.api.env.API_KEY",
        "workloads.web.containers.api.env.LOG_LEVEL",
        "workloads.web.containers.api.env.WORKERS",
    ]


def test_restore_keeps_the_callers_own_container_env_edits() -> None:
    edited = MANIFEST_MASKED.replace(f'LOG_LEVEL = "{_V}"', 'LOG_LEVEL = "debug"').replace(
        f'env.DB_PASSWORD = "{_V}"', f'env.DB_PASSWORD = "{_V}"\nenv.NEW_KEY = "typed"'
    )
    resolved, missing = resolve_masked_env_values(edited, fallback_text=MANIFEST)
    assert missing == []
    assert resolved == MANIFEST.replace("LOG_LEVEL = 'info'", 'LOG_LEVEL = "debug"').replace(
        f'env.DB_PASSWORD = "{_JOB_PASSWORD}"', f'env.DB_PASSWORD = "{_JOB_PASSWORD}"\nenv.NEW_KEY = "typed"'
    )


def test_restore_refuses_every_container_placeholder_on_create() -> None:
    """registerApp has no stored document to fall back to."""
    resolved, missing = resolve_masked_env_values(MANIFEST_MASKED, fallback_text="")
    assert resolved is None
    assert "workloads.web.containers.app.env.API_KEY" in missing
    assert "jobs.nightly.env.DB_PASSWORD" in missing
    assert "tasks.migrate.env.TOKEN" in missing


# ---- text tomllib rejects -----------------------------------------------

_BROKEN = f"""\
name = "hello"
replicas = = 2

[[workloads]]
name = "web"

  [[workloads.containers]]
  name = "app"

    [workloads.containers.env]
    # old: {_OLD_WEB_KEY}
    API_KEY = "{_WEB_KEY}"

[jobs.env]
DB_PASSWORD = '{_JOB_PASSWORD}'
"""


def test_unparseable_text_masks_container_env_sections_line_by_line() -> None:
    assert redact_env_values(_BROKEN) == (
        _BROKEN.replace(f"# old: {_OLD_WEB_KEY}", _marker(1))
        .replace(f'"{_WEB_KEY}"', f'"{_V}"')
        .replace(f"'{_JOB_PASSWORD}'", f'"{_V}"')
    )


@pytest.mark.parametrize(
    "text",
    [
        # An inline env table under a container: the line scan can't read it.
        f'name = "x"\nbad = = 1\n[[workloads.containers]]\nenv = {{ API_KEY = "{_WEB_KEY}" }}\n',
        # A dotted env key under a job.
        f'name = "x"\nbad = = 1\n[[jobs]]\nenv.DB_PASSWORD = "{_JOB_PASSWORD}"\n',
        # A header nesting under a container env.
        f'name = "x"\nbad = = 1\n[workloads.containers.env.NESTED]\nK = "{_WEB_KEY}"\n',
    ],
)
def test_unparseable_text_fails_closed_on_container_env_it_cannot_read(text: str) -> None:
    assert redact_env_values(text) == REDACTED_DOCUMENT


# ---- parse_raw -------------------------------------------------------------


@pytest.mark.parametrize(
    ("placeholder_at", "path", "label"),
    [
        (f'API_KEY   = "{_WEB_KEY}"', "workloads[0].containers[0].env.API_KEY", "containers.app.env.API_KEY"),
        (f'API_KEY = "{_SIDECAR_KEY}"', "workloads[0].containers[1].env.API_KEY", "sidecar.env.API_KEY"),
        (f'env.DB_PASSWORD = "{_JOB_PASSWORD}"', "jobs[0].env.DB_PASSWORD", "jobs.nightly.env.DB_PASSWORD"),
        (f'TOKEN = """{_TASK_TOKEN}"""', "tasks[0].env.TOKEN", "tasks.migrate.env.TOKEN"),
    ],
)
def test_parse_raw_refuses_the_placeholder_in_container_job_and_task_env(
    placeholder_at: str, path: str, label: str
) -> None:
    """Apply, sync and deploy all parse first, so a placeholder nobody
    put back fails loudly instead of replacing the secret."""
    key, _, _ = placeholder_at.partition("=")
    text = MANIFEST.replace(placeholder_at, f'{key.rstrip()} = "{_V}"')
    with pytest.raises(ManifestError) as exc_info:
        parse_raw(text)
    assert exc_info.value.path == path
    assert label in str(exc_info.value)
    assert "masked placeholder" in str(exc_info.value)
    for secret in _SECRETS:
        assert secret not in str(exc_info.value)
