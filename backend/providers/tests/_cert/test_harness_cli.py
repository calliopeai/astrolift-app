"""CLI authorization checks stop before constructing cloud inventory clients."""

import pytest

from _cert.harness import __main__ as cli


@pytest.mark.parametrize("flag", ["--estimate-logged", "--ack-estimate"])
def test_old_monetary_flags_are_rejected_before_cloud_calls(flag, capsys, monkeypatch):
    monkeypatch.setattr(cli, "_scan_for", lambda *args: pytest.fail("cloud inventory constructed"))
    with pytest.raises(SystemExit) as error:
        cli.main([flag])
    assert error.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


def test_capacity_authorization_does_not_replace_explicit_cloud_credentials(capsys, monkeypatch):
    monkeypatch.setattr(cli, "_scan_for", lambda *args: pytest.fail("cloud inventory constructed"))
    with pytest.raises(SystemExit) as error:
        cli.main(["--allow-capacity-resources", "--project-id", "synthetic-project"])
    assert error.value.code == 2
    assert "refusing to run without --i-have-credentials" in capsys.readouterr().err


def test_exact_project_selection_is_still_required(capsys, monkeypatch):
    monkeypatch.setattr(cli, "_scan_for", lambda *args: pytest.fail("cloud inventory constructed"))
    with pytest.raises(SystemExit) as error:
        cli.main(["--i-have-credentials", "--allow-capacity-resources"])
    assert error.value.code == 2
    assert "--project-id is required" in capsys.readouterr().err


def test_help_describes_resource_authorization_without_claiming_capacity_permission_or_money(capsys):
    with pytest.raises(SystemExit) as error:
        cli.main(["--help"])
    assert error.value.code == 0
    help_text = " ".join(capsys.readouterr().out.split())
    assert "--allow-capacity-resources" in help_text
    assert "does not prove observed capacity, provider permission or a financial bound" in help_text
    assert "--estimate-logged" not in help_text and "--ack-estimate" not in help_text
