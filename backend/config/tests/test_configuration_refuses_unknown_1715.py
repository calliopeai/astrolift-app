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
