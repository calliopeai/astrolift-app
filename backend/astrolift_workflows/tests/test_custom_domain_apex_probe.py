"""The probe for a custom domain whose hostname is a zone apex.

An apex cannot hold a CNAME, so the platform writes an ALIAS (an A record) to
the ingress load balancer. The handshake still lists the required record as a
CNAME. The probe has to check the ALIAS, or validation never passes for an
apex domain: it reports "no CNAME record" for ever.
"""

from __future__ import annotations

import pytest

from astrolift_workflows.activities import custom_domain

CNAME_ROW = {"kind": "CNAME", "name": "example.com.", "value": "ingress.example.com.", "ttl": 300}


def _resolver(answers):
    def resolve(*, apex, name, record_type):
        return answers[(name.rstrip("."), record_type)]

    return resolve


def test_an_apex_alias_that_matches_the_target_counts_as_propagated(monkeypatch) -> None:
    monkeypatch.setattr(
        custom_domain,
        "_resolve_authoritative_records",
        _resolver(
            {
                ("example.com", "A"): (["52.1.1.1", "16.2.2.2"], ""),
                ("ingress.example.com", "A"): (["16.2.2.2", "52.1.1.1"], ""),
            }
        ),
    )
    row = custom_domain._probe_one_record(CNAME_ROW, "example.com")
    assert row["propagated"] is True
    assert row["message"] == "apex ALIAS propagated"


def test_an_apex_that_points_elsewhere_is_not_propagated(monkeypatch) -> None:
    monkeypatch.setattr(
        custom_domain,
        "_resolve_authoritative_records",
        _resolver(
            {
                ("example.com", "A"): (["203.0.113.9"], ""),
                ("ingress.example.com", "A"): (["16.2.2.2"], ""),
            }
        ),
    )
    row = custom_domain._probe_one_record(CNAME_ROW, "example.com")
    assert row["propagated"] is False
    assert "203.0.113.9" in row["message"]


@pytest.mark.parametrize("missing", ["apex", "target"])
def test_a_missing_apex_or_target_record_reports_its_error(monkeypatch, missing) -> None:
    answers = {
        ("example.com", "A"): (["16.2.2.2"], ""),
        ("ingress.example.com", "A"): (["16.2.2.2"], ""),
    }
    key = ("example.com", "A") if missing == "apex" else ("ingress.example.com", "A")
    answers[key] = ([], f"no A record at {key[0]!r} at the authoritative NS")
    monkeypatch.setattr(custom_domain, "_resolve_authoritative_records", _resolver(answers))
    row = custom_domain._probe_one_record(CNAME_ROW, "example.com")
    assert row["propagated"] is False
    assert row["message"].startswith("no A record at")


def test_a_cname_below_the_apex_is_still_checked_as_a_cname(monkeypatch) -> None:
    row = {"kind": "CNAME", "name": "www.example.com.", "value": "ingress.example.com.", "ttl": 300}
    monkeypatch.setattr(
        custom_domain,
        "_resolve_authoritative_records",
        _resolver({("www.example.com", "CNAME"): (["ingress.example.com"], "")}),
    )
    out = custom_domain._probe_one_record(row, "www.example.com")
    assert out["propagated"] is True
    assert out["message"] == "CNAME record propagated"
