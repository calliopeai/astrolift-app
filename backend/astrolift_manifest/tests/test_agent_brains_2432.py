"""An agent's ``brains`` and ``brains_allowed`` (#2432): the brains every run
a person launches reads, and the ones a launch may add. Whether the launcher
can read them is decided at launch; the manifest only names them."""

from __future__ import annotations

import pytest

from astrolift_manifest.normalize import normalize
from astrolift_manifest.parser import MAX_AGENT_BRAINS, ManifestError, parse_raw

_AGENT = """
name = "support"

[[workloads]]
name = "triage"
kind = "agent"
{extra}
"""


def _agent(extra: str = ""):
    return parse_raw(_AGENT.format(extra=extra)).workloads[0]


def test_an_agent_without_them_reads_no_brains_and_accepts_any():
    workload = _agent()
    assert (workload.brains, workload.brains_allowed) == ((), None)


def test_the_brains_and_the_allow_list_keep_their_order():
    workload = _agent('brains = ["support-kb", " eng-kb "]\nbrains_allowed = ["sales-kb", "hr-kb"]')
    assert workload.brains == ("support-kb", "eng-kb")
    assert workload.brains_allowed == ("sales-kb", "hr-kb")


def test_an_empty_allow_list_accepts_no_brains_rather_than_any():
    assert _agent("brains_allowed = []").brains_allowed == ()


@pytest.mark.parametrize(
    ("extra", "path", "says"),
    [
        ('brains = "support-kb"', "workloads[0].brains", "must be a list of brain slugs"),
        ('brains = ["Support_KB"]', "workloads[0].brains[0]", "must be the slug of a hosted brain"),
        ('brains = ["kb", ""]', "workloads[0].brains[1]", "must be the slug of a hosted brain"),
        ("brains = [7]", "workloads[0].brains[0]", "must be the slug of a hosted brain"),
        ('brains = ["kb", "kb"]', "workloads[0].brains[1]", "is listed twice"),
        ('brains_allowed = ["kb-"]', "workloads[0].brains_allowed[0]", "must be the slug of a hosted brain"),
        (
            "brains = [" + ", ".join(f'"kb{i}"' for i in range(MAX_AGENT_BRAINS + 1)) + "]",
            "workloads[0].brains",
            f"at most {MAX_AGENT_BRAINS}",
        ),
    ],
)
def test_a_malformed_list_is_refused_where_it_is(extra, path, says):
    with pytest.raises(ManifestError) as exc:
        _agent(extra)
    assert exc.value.path == path
    assert says in str(exc.value)


def test_brains_on_a_workload_that_is_not_an_agent_are_refused():
    toml = '\nname = "web"\n\n[[workloads]]\nname = "web"\nkind = "deployment"\nbrains = ["kb"]\n'
    with pytest.raises(ManifestError, match="apply to agent workloads only"):
        parse_raw(toml)


def test_only_a_declared_list_reaches_the_manifest_hash():
    plain = normalize(parse_raw(_AGENT.format(extra=""))).serialized["workloads"][0]
    assert "brains" not in plain and "brains_allowed" not in plain

    declared = normalize(parse_raw(_AGENT.format(extra='brains = ["kb"]\nbrains_allowed = []')))
    workload = declared.serialized["workloads"][0]
    assert (workload["brains"], workload["brains_allowed"]) == (["kb"], [])
