"""Actual isolated production settings import, without overriding Django fields."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("mode", ["default", "starttls", "implicit"])
def test_install_smtp_environment_loads_actual_django_settings(mode):
    env = os.environ.copy()
    for name in (
        "DJANGO_EMAIL_BACKEND",
        "EMAIL_USE_TLS",
        "EMAIL_USE_SSL",
        "EMAIL_HOST_USER",
        "EMAIL_HOST_PASSWORD",
        "EMAIL_SSL_KEYFILE",
        "EMAIL_SSL_CERTFILE",
    ):
        env.pop(name, None)
    env.update(
        DOTENV="/dev/null", DJANGO_CONFIGURATION="Tests", DJANGO_SETTINGS_MODULE="config.test_settings"
    )
    if mode != "default":
        env.update(
            DJANGO_EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
            EMAIL_USE_TLS=str(mode == "starttls"),
            EMAIL_USE_SSL=str(mode == "implicit"),
            EMAIL_HOST_USER="private-settings-user-marker",
            EMAIL_HOST_PASSWORD="private-settings-password-marker",
            EMAIL_SSL_KEYFILE="/private/fixture-client-key",
            EMAIL_SSL_CERTFILE="/private/fixture-client-cert",
        )
    script = """
from django.conf import settings
import sys
mode = sys.argv[1]
assert settings.EMAIL_USE_TLS is (mode == 'starttls')
assert settings.EMAIL_USE_SSL is (mode == 'implicit')
if mode == 'default':
    assert settings.EMAIL_BACKEND == 'django_ses.SESBackend'
    assert settings.EMAIL_HOST_USER == settings.EMAIL_HOST_PASSWORD == ''
    assert settings.EMAIL_SSL_KEYFILE is settings.EMAIL_SSL_CERTFILE is None
else:
    assert settings.EMAIL_BACKEND == 'django.core.mail.backends.smtp.EmailBackend'
    assert settings.EMAIL_HOST_USER == 'private-settings-user-marker'
    assert settings.EMAIL_HOST_PASSWORD == 'private-settings-password-marker'
    assert settings.EMAIL_SSL_KEYFILE == '/private/fixture-client-key'
    assert settings.EMAIL_SSL_CERTFILE == '/private/fixture-client-cert'
"""
    result = subprocess.run(
        [sys.executable, "-c", script, mode],
        env=env,
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0
    assert "private-settings" not in result.stdout + result.stderr
