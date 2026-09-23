"""An unrecognised DJANGO_CONFIGURATION must refuse to start (#1715).

The bare ``else`` in settings.py used to hand any unknown name an in-memory
SQLite database and a hardcoded SECRET_KEY. A deployment that invented a
configuration name came up, served its login page and passed its load balancer
health check while every write went to a database inside one task: two tasks
disagreed, a restart was a factory reset, and migrations ran against a schema
thrown away at exit. Every POSTGRES_* variable was correct throughout.

None of that is visible from outside the process, which is what makes silence
the wrong default -- a typo in one environment variable becomes data loss that
looks like a healthy install.

Run in a subprocess: settings are imported once per process, so asserting on
start-up behaviour means actually starting one.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]

_IMPORT_SETTINGS = "import config.settings"


def _start_with(configuration: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", _IMPORT_SETTINGS],
        cwd=BACKEND,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "DJANGO_CONFIGURATION": configuration,
            "DJANGO_SETTINGS_MODULE": "config.settings",
            "PYTHONPATH": f"{BACKEND}:{BACKEND / 'providers'}",
        },
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_an_invented_configuration_name_refuses_to_start():
    """ "Production" is not a name this application declares. It is the exact
    value that caused the incident."""
    result = _start_with("Production")
    assert result.returncode != 0, "started on a configuration it does not declare"
    assert "not a configuration this application declares" in result.stderr


def test_the_refusal_names_the_valid_values():
    """An operator hitting this needs to know what to put instead, without
    reading settings.py."""
    result = _start_with("Production")
    assert "prd" in result.stderr and "stg" in result.stderr


def test_the_refusal_explains_the_consequence_it_prevents():
    """Refusing without saying why invites someone to 'fix' it by picking any
    name that happens to be accepted."""
    result = _start_with("Production")
    assert "in-memory" in result.stderr or "loses every write" in result.stderr


@pytest.mark.parametrize("name", ["dev", "prd", "stg", "int", "local"])
def test_declared_configurations_still_start(name):
    """The guard must not break the names that are actually in use."""
    result = _start_with(name)
    assert "not a configuration this application declares" not in result.stderr


# ---------------------------------------------------------------------
# The server configurations do not run with the developer's error page
# (#1732)
# ---------------------------------------------------------------------

_PRINT_POSTURE = (
    "import json, config.settings as s; "
    "print(json.dumps({'debug': s.DEBUG, 'validators': len(s.AUTH_PASSWORD_VALIDATORS)}))"
)


def _posture(configuration: str | None, **extra_env: str) -> dict:
    """Import settings the way a deployment does and report its posture.

    The POSTGRES_* variables are set because a server configuration
    refuses to start without a database engine -- correctly, and a test
    that imports without them is testing the refusal, not the posture.
    Nothing connects; ``DATABASES`` is only read.

    ``configuration=None`` omits DJANGO_CONFIGURATION entirely, mirroring
    an install that never declares one (#1732) rather than one that spells
    the name wrong. ``**extra_env`` layers on top, e.g. DJANGO_DEBUG or a
    DOTENV pointed at a path that does not exist.
    """
    import json

    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "DJANGO_SETTINGS_MODULE": "config.settings",
        "PYTHONPATH": f"{BACKEND}:{BACKEND / 'providers'}",
        "POSTGRES_ENGINE": "django.db.backends.postgresql",
        "POSTGRES_DB": "astrolift",
        "POSTGRES_USER": "astrolift",
        "POSTGRES_PASSWORD": "unused-by-an-import",
        "POSTGRES_HOST": "postgres.invalid",
        "POSTGRES_PORT": "5432",
        **extra_env,
    }
    if configuration is not None:
        env["DJANGO_CONFIGURATION"] = configuration
    result = subprocess.run(
        [sys.executable, "-c", _PRINT_POSTURE],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("name", ["prd", "stg", "int"])
def test_a_server_configuration_does_not_run_with_debug(name):
    """DEBUG=True serves Django's technical 500 page -- traceback, every
    frame's locals, and the settings dump redacted only by name-matching --
    to whoever provoked the exception."""
    assert _posture(name)["debug"] is False


@pytest.mark.parametrize("name", ["prd", "stg", "int"])
def test_a_server_configuration_keeps_its_password_validators(name):
    """The same block emptied AUTH_PASSWORD_VALIDATORS, so every password
    rule the project declares was off on the configurations that need them."""
    assert _posture(name)["validators"] > 0


def test_the_developer_configuration_keeps_the_technical_error_page(tmp_path):
    """The trade is right on a laptop and wrong on a server; this is the
    laptop. DJANGO_DEBUG=1 is what every dev/test surface in this repo sets
    explicitly (local.env, docker-compose.yaml, CI's backend-test-shard job)
    -- that explicit opt-in is what makes it a laptop, rather than an
    install that forgot to declare a configuration (#1732)."""
    posture = _posture("dev", DJANGO_DEBUG="1", DOTENV=str(tmp_path / "absent.env"))
    assert posture["debug"] is True


# ---------------------------------------------------------------------
# An undeclared configuration is not a developer laptop (#1732)
# ---------------------------------------------------------------------


def test_an_undeclared_configuration_does_not_enable_debug(tmp_path):
    """CONFIGURATION defaults to "Dev" a few lines into settings.py when
    DJANGO_CONFIGURATION is unset -- exactly the SteadyMD install's
    situation (finding 1 on #1732). DEBUG must not silently follow that
    default; only an explicit DJANGO_DEBUG opts in."""
    posture = _posture(None, DOTENV=str(tmp_path / "absent.env"))
    assert posture["debug"] is False


def test_an_undeclared_configuration_keeps_its_password_validators(tmp_path):
    """The same silent default used to clear AUTH_PASSWORD_VALIDATORS along
    with DEBUG, so an install that never declared a configuration kept
    every password rule off too."""
    posture = _posture(None, DOTENV=str(tmp_path / "absent.env"))
    assert posture["validators"] > 0


def test_explicit_dev_without_debug_flag_does_not_enable_debug(tmp_path):
    """ "dev" is never set explicitly anywhere in this repo's own configs --
    it is only ever reached through the default above -- but an operator
    who does set it gets the same safe posture unless they also opt into
    DJANGO_DEBUG."""
    posture = _posture("dev", DOTENV=str(tmp_path / "absent.env"))
    assert posture["debug"] is False


def test_an_undeclared_configuration_refuses_sqlite(tmp_path):
    """The #1715 SQLite guard used to be inert here: CONFIGURATION silently
    resolved to "Dev", and IS_DEV excused every dev-labelled configuration
    from the guard. An install that never declares DJANGO_CONFIGURATION is
    exactly the SteadyMD install's situation (#1732) -- the guard must hold
    for it, not just for configurations that spell their name correctly.
    """
    result = subprocess.run(
        [sys.executable, "-c", _IMPORT_SETTINGS],
        cwd=BACKEND,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "DJANGO_SETTINGS_MODULE": "config.settings",
            "PYTHONPATH": f"{BACKEND}:{BACKEND / 'providers'}",
            "POSTGRES_ENGINE": "django.db.backends.sqlite3",
            "POSTGRES_DB": ":memory:",
            "DOTENV": str(tmp_path / "absent.env"),
        },
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode != 0
    assert "must not run on SQLite" in result.stderr


def test_a_job_with_no_real_database_can_declare_the_dummy_engine(tmp_path):
    """Re-keying the #1715 guard onto DEBUG (#1732) means a caller with no
    real database -- the CI `contracts` job introspects schema and never
    connects to one -- must say so explicitly rather than relying on an
    undeclared configuration reading as a developer laptop. This is the same
    POSTGRES_ENGINE=django.db.backends.dummy pattern the Dockerfile's
    collectstatic step already relies on."""
    result = subprocess.run(
        [sys.executable, "-c", _IMPORT_SETTINGS],
        cwd=BACKEND,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "DJANGO_SETTINGS_MODULE": "config.settings",
            "PYTHONPATH": f"{BACKEND}:{BACKEND / 'providers'}",
            "POSTGRES_ENGINE": "django.db.backends.dummy",
            "DOTENV": str(tmp_path / "absent.env"),
        },
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-2000:]


def test_a_server_configuration_with_no_database_engine_refuses_to_start(tmp_path):
    """The membership test in the SQLite guard raised TypeError on an unset
    engine -- an unhandled crash from the guard written to make exactly this
    situation readable.

    ``DOTENV`` points at a path that does not exist so the outcome does not
    depend on whether the machine running the tests happens to have a
    ``config/local.env`` full of POSTGRES_* values.
    """
    result = subprocess.run(
        [sys.executable, "-c", _IMPORT_SETTINGS],
        cwd=BACKEND,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "DJANGO_CONFIGURATION": "prd",
            "DJANGO_SETTINGS_MODULE": "config.settings",
            "PYTHONPATH": f"{BACKEND}:{BACKEND / 'providers'}",
            "DOTENV": str(tmp_path / "absent.env"),
        },
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode != 0
    assert "no database engine configured" in result.stderr
    assert "TypeError" not in result.stderr
