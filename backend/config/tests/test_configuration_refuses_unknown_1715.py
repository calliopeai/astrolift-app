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


def _posture(configuration: str) -> dict:
    """Import settings the way a deployment does and report its posture.

    The POSTGRES_* variables are set because a server configuration
    refuses to start without a database engine -- correctly, and a test
    that imports without them is testing the refusal, not the posture.
    Nothing connects; ``DATABASES`` is only read.
    """
    import json

    result = subprocess.run(
        [sys.executable, "-c", _PRINT_POSTURE],
        cwd=BACKEND,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "DJANGO_CONFIGURATION": configuration,
            "DJANGO_SETTINGS_MODULE": "config.settings",
            "PYTHONPATH": f"{BACKEND}:{BACKEND / 'providers'}",
            "POSTGRES_ENGINE": "django.db.backends.postgresql",
            "POSTGRES_DB": "astrolift",
            "POSTGRES_USER": "astrolift",
            "POSTGRES_PASSWORD": "unused-by-an-import",
            "POSTGRES_HOST": "postgres.invalid",
            "POSTGRES_PORT": "5432",
        },
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


def test_the_developer_configuration_keeps_the_technical_error_page():
    """The trade is right on a laptop and wrong on a server; this is the
    laptop."""
    assert _posture("dev")["debug"] is True


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
