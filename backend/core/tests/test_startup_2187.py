"""Run the container entry script without launching subprocesses or servers."""

from __future__ import annotations

import builtins
import runpy
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

STARTUP = Path(__file__).resolve().parents[2] / "startup.py"


@pytest.fixture
def startup(monkeypatch, tmp_path):
    commands = []
    system_calls = []
    results = {}
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DJANGO_CONFIGURATION", "prd")
    monkeypatch.setitem(sys.modules, "uvicorn", SimpleNamespace())
    monkeypatch.setattr("os.system", lambda command: system_calls.append(command) or 0)

    def run_command(command, *, shell, text, capture_output):
        assert shell and text and capture_output
        commands.append(command)
        code = results.get(command.removeprefix("python manage.py "), 0)
        return subprocess.CompletedProcess(command, code, stdout="", stderr="failed" if code else "")

    monkeypatch.setattr(subprocess, "run", run_command)
    return SimpleNamespace(
        run=lambda: runpy.run_path(str(STARTUP), run_name="__main__"),
        commands=commands,
        system_calls=system_calls,
        results=results,
    )


def test_migration_failure_exits_before_bootstrap_or_server(startup, caplog):
    startup.results["migrate"] = 7
    with pytest.raises(SystemExit) as error:
        startup.run()
    assert error.value.code == 7
    assert startup.commands == ["python manage.py showmigrations", "python manage.py migrate"]
    assert startup.system_calls == ["service cron start"]
    assert "migration failed" in caplog.text.lower()


@pytest.mark.parametrize(
    ("configuration", "reload"),
    [("prd", False), ("stg", False), ("int", False), ("Dev", True), ("LocalPg", True), (None, True)],
)
def test_successful_migration_preserves_bootstrap_and_server(startup, monkeypatch, configuration, reload):
    if configuration is None:
        monkeypatch.delenv("DJANGO_CONFIGURATION")
    else:
        monkeypatch.setenv("DJANGO_CONFIGURATION", configuration)
    startup.run()
    assert startup.commands[:2] == ["python manage.py showmigrations", "python manage.py migrate"]
    assert "python manage.py bootstrap_admin" in startup.commands
    assert startup.commands.index("python manage.py bootstrap_admin") < startup.commands.index(
        "python manage.py bootstrap_idp"
    )
    assert startup.commands[-1] == "python manage.py bootstrap_managed_domain"
    expected = "uvicorn config.asgi:application --host 0.0.0.0 --port 8000"
    assert startup.system_calls == ["service cron start", expected + (" --reload" if reload else "")]


@pytest.mark.parametrize(
    "optional_command", ["showmigrations", "collectstatic --noinput", "register_tenant_cluster"]
)
def test_non_migration_failure_keeps_existing_optional_command_behavior(startup, optional_command):
    startup.results[optional_command] = 9
    startup.run()
    assert startup.commands[-1] == "python manage.py bootstrap_managed_domain"
    assert startup.system_calls[-1] == "uvicorn config.asgi:application --host 0.0.0.0 --port 8000"


def test_successful_migration_preserves_runserver_fallback_without_uvicorn(startup, monkeypatch):
    original_import = builtins.__import__

    def import_without_uvicorn(name, *args, **kwargs):
        if name == "uvicorn":
            raise ImportError("uvicorn unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_uvicorn)
    startup.run()
    assert startup.system_calls == ["service cron start", "python manage.py runserver 0.0.0.0:8000"]
