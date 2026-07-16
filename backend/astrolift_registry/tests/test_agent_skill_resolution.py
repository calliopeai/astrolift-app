"""Skill + brief resolution at agent registration (spec 38 Phase 3 + spec 39c).

Exercises ``register_agent_repo``'s resolution wiring end-to-end against real
Postgres, with injected fixture repo trees + a mocked catalogue tree (never a
real GitHub call):

  1. an agent with a LOCAL skill + a NAMED (catalogue) skill + a brief →
     Skills upserted in the org, AgentSkillRef set + positions, Workload.brief
     set, BriefSkillRef links;
  2. re-scan is idempotent — no duplicate Skills / refs / Briefs, and a skill
     removed from the manifest has its AgentSkillRef reconciled away;
  3. a named skill absent from the catalogue → the agent still registers and
     the failure is recorded as a non-fatal note;
  4. a catalogue fetch outage → local skills still resolve, named skills are
     skipped with a note, the agent still registers;
  5. tenancy — every resolved Skill / Brief lands in the agent's own org.

The catalogue fetch is patched at ``load_catalogue_tree`` so the production
resolver path runs with only that one network boundary stubbed.
"""

from __future__ import annotations

import pytest

from astrolift_agents.models import AgentSkillRef, Brief, BriefSkillRef, OrgSkillRepo, Skill
from astrolift_identity.models import Organization, Project, Team
from astrolift_registry.models import Workload
from astrolift_registry.services.manifest_sync import register_agent_repo, resync_agent_repo_manifests
from astrolift_scm.models import SourceConnection

pytestmark = pytest.mark.django_db


# ---- fixtures ---------------------------------------------------------


def _skill_md(name: str, description: str, body: str) -> str:
    # Quote the description: real SKILL.md descriptions routinely contain a
    # colon ("Review a PR: ...") which is invalid as bare YAML.
    return f'---\nname: {name}\ndescription: "{description}"\n---\n\n{body}\n'


def _agent_toml_with_brief_and_skills() -> str:
    """An agent manifest declaring a brief, one local skill, one named skill."""
    return (
        'name = "triage"\n'
        'brief = "brief/README.md"\n'
        "skills = [\n"
        '  { reviewer = "skills/reviewer" },\n'  # local
        '  "pr-review",\n'  # named (catalogue)
        "]\n"
        "[[workloads]]\n"
        'name = "triage"\n'
        'kind = "agent"\n'
        "[[workloads.containers]]\n"
        'name = "triage"\n'
        "is_primary = true\n"
        'image_ref = "ecr.example/agent:latest"\n'
    )


def _agent_repo_tree(toml_text: str | None = None) -> dict[str, str]:
    """Repo tree for the triage agent: its manifest, a local skill, a brief."""
    return {
        "agents/triage/astrolift.toml": toml_text or _agent_toml_with_brief_and_skills(),
        # Local skill folder (agentskills.io layout), repo-relative.
        "skills/reviewer/SKILL.md": _skill_md(
            "Reviewer",
            "Reviews code changes for correctness.",
            "Read the diff. Comment on risks.",
        ),
        "skills/reviewer/scripts/run.sh": "echo review",
        # Brief folder fronted by README.md, linking a sibling spec.
        "brief/README.md": "# Triage brief\n\nSee [the spec](spec.md) for context.\n",
        "brief/spec.md": "## Spec\n\nTriage incoming issues.\n",
        "README.md": "repo docs",
    }


def _catalogue_tree() -> dict[str, str]:
    """A mocked built-in catalogue with the pr-review skill."""
    return {
        "skills/pr-review/SKILL.md": _skill_md(
            "PR Review",
            "Review a pull request: diff analysis, inline findings, summary.",
            "Analyze the PR diff and summarize findings.",
        ),
        "skills/pr-review/scripts/review.py": "print('review')",
        "skills/http-request/SKILL.md": _skill_md("HTTP Request", "REST/HTTP client.", "Make HTTP requests."),
        "catalogue.json": '{"skills": []}',
    }


@pytest.fixture
def org():
    return Organization.objects.create(name="Acme", slug="acme")


@pytest.fixture
def other_org():
    return Organization.objects.create(name="Globex", slug="globex")


def _project(org, *, slug="demo"):
    team = Team.objects.create(organization=org, name=f"Eng-{slug}", slug=f"eng-{slug}")
    return Project.objects.create(organization=org, team=team, name=slug.title(), slug=slug)


@pytest.fixture
def with_connection():
    def _make(org):
        return SourceConnection.objects.create(
            organization=org,
            kind=SourceConnection.Kind.GITHUB_PAT,
            display_name=f"{org.slug} PAT",
            account_login=org.slug,
        )

    return _make


def _tree_of(files: dict[str, str]):
    def _tree(connection, repo_full_name, ref):
        return dict(files)

    return _tree


@pytest.fixture
def mock_catalogue(monkeypatch):
    """Patch the catalogue fetch to return a fixed tree (no network).

    ``manifest_sync._load_catalogue_if_needed`` lazily imports
    ``load_catalogue_tree`` from the resolver module inside the function, so
    patching the resolver module symbol is sufficient and exercises the real
    "is a named skill present? -> fetch once" gate. Returns a ``calls`` dict so
    a test can assert the catalogue was fetched exactly once per pass.
    """

    def _install(tree: dict[str, str] | None):
        calls = {"n": 0}

        def _load():
            calls["n"] += 1
            if tree is None:
                from astrolift_agents.services.skill_resolver import CatalogueFetchError

                raise CatalogueFetchError("catalogue unreachable in test", path="calliopeai/astrolift-skills")
            return dict(tree)

        from astrolift_agents.services import skill_resolver

        monkeypatch.setattr(skill_resolver, "load_catalogue_tree", _load)
        return calls

    return _install


def _register(project, *, files, repo="acme/agents"):
    return register_agent_repo(
        project=project,
        source_kind="github",
        source_repo=repo,
        ref="main",
        tree=_tree_of(files),
    )


# ---------------------------------------------------------------------------
# Case 1: local + named skill + brief all resolve and persist
# ---------------------------------------------------------------------------


def test_local_named_skill_and_brief_resolve_and_persist(org, with_connection, mock_catalogue):
    with_connection(org)
    project = _project(org)
    calls = mock_catalogue(_catalogue_tree())

    result = _register(project, files=_agent_repo_tree())

    assert result.status == "ok"
    [agent] = result.agents
    assert agent.skill_notes == []  # everything resolved cleanly
    # Catalogue fetched exactly once for the pass (a named skill was present).
    assert calls["n"] == 1

    # Two org-scoped Skills upserted, slugged from the SKILL.md names.
    skills = {s.slug: s for s in Skill.objects.filter(organization=org, deleted_at__isnull=True)}
    assert set(skills) == {"reviewer", "pr-review"}
    assert skills["reviewer"].name == "Reviewer"
    assert skills["reviewer"].content == "Read the diff. Comment on risks."
    assert skills["pr-review"].name == "PR Review"
    assert skills["pr-review"].content == "Analyze the PR diff and summarize findings."
    # Both versioned at v1 on first import.
    assert skills["reviewer"].skill_version == 1
    assert skills["pr-review"].skill_version == 1

    # AgentSkillRef set + manifest-order positions (local first, named second).
    workload = Workload.objects.get(
        kind=Workload.Kind.AGENT, registered_app__organization=org, deleted_at__isnull=True
    )
    refs = {
        r.skill.slug: r.position
        for r in AgentSkillRef.objects.filter(workload=workload, deleted_at__isnull=True)
    }
    assert refs == {"reviewer": 0, "pr-review": 1}

    # Brief assembled, READY, linked to the workload, scoped to the org.
    workload.refresh_from_db()
    assert workload.brief_id is not None
    brief = Brief.objects.get(id=workload.brief_id)
    assert brief.organization_id == org.id
    assert brief.status == Brief.Status.READY
    assert brief.context["brief_readme"].startswith("# Triage brief")
    # The README's in-folder reference was surfaced.
    assert brief.context["brief_referenced_paths"] == ["brief/spec.md"]

    # BriefSkillRef links both resolved skills, snapshotting their versions.
    brief_skill_slugs = {
        bsr.skill.slug: bsr.skill_version for bsr in BriefSkillRef.objects.filter(brief=brief)
    }
    assert brief_skill_slugs == {"reviewer": 1, "pr-review": 1}


# ---------------------------------------------------------------------------
# Case 2: re-scan is idempotent + reconciles a removed skill
# ---------------------------------------------------------------------------


def test_rescan_is_idempotent_no_duplicates(org, with_connection, mock_catalogue):
    with_connection(org)
    project = _project(org)
    mock_catalogue(_catalogue_tree())
    files = _agent_repo_tree()

    first = _register(project, files=files)
    assert first.status == "ok"
    brief_id_first = Workload.objects.get(kind=Workload.Kind.AGENT, registered_app__organization=org).brief_id

    # Re-scan the identical repo: no new Skills / refs / Briefs.
    rescan = resync_agent_repo_manifests(project=project, source_repo="acme/agents", tree=_tree_of(files))
    assert rescan.status == "ok"

    assert Skill.objects.filter(organization=org, deleted_at__isnull=True).count() == 2
    workload = Workload.objects.get(kind=Workload.Kind.AGENT, registered_app__organization=org)
    assert AgentSkillRef.objects.filter(workload=workload, deleted_at__isnull=True).count() == 2
    # Unchanged content + skills → same content-addressed Brief reused.
    workload.refresh_from_db()
    assert workload.brief_id == brief_id_first
    assert Brief.objects.filter(organization=org).count() == 1
    # Skill versions did not churn on the no-op re-scan.
    assert {s.skill_version for s in Skill.objects.filter(organization=org)} == {1}


def test_rescan_reconciles_removed_skill(org, with_connection, mock_catalogue):
    with_connection(org)
    project = _project(org)
    mock_catalogue(_catalogue_tree())

    _register(project, files=_agent_repo_tree())
    workload = Workload.objects.get(kind=Workload.Kind.AGENT, registered_app__organization=org)
    assert AgentSkillRef.objects.filter(workload=workload, deleted_at__isnull=True).count() == 2

    # Drop the named skill from the manifest (keep only the local one).
    toml_one_skill = (
        'name = "triage"\n'
        'brief = "brief/README.md"\n'
        "skills = [\n"
        '  { reviewer = "skills/reviewer" },\n'
        "]\n"
        "[[workloads]]\n"
        'name = "triage"\n'
        'kind = "agent"\n'
        "[[workloads.containers]]\n"
        'name = "triage"\n'
        "is_primary = true\n"
        'image_ref = "ecr.example/agent:latest"\n'
    )
    files = _agent_repo_tree(toml_text=toml_one_skill)
    rescan = resync_agent_repo_manifests(project=project, source_repo="acme/agents", tree=_tree_of(files))
    assert rescan.status == "ok"

    # The pr-review ref is reconciled away; only reviewer remains active.
    active = {r.skill.slug for r in AgentSkillRef.objects.filter(workload=workload, deleted_at__isnull=True)}
    assert active == {"reviewer"}
    # The Skill rows themselves are NOT hard-deleted (only the join is dropped).
    assert Skill.objects.filter(organization=org, deleted_at__isnull=True).count() == 2
    # The Brief was re-assembled to drop the removed skill's BriefSkillRef link.
    workload.refresh_from_db()
    brief = Brief.objects.get(id=workload.brief_id)
    assert {bsr.skill.slug for bsr in BriefSkillRef.objects.filter(brief=brief)} == {"reviewer"}


# ---------------------------------------------------------------------------
# Case 3: a named skill missing from the catalogue is non-fatal
# ---------------------------------------------------------------------------


def test_named_skill_missing_from_catalogue_is_non_fatal(org, with_connection, mock_catalogue):
    with_connection(org)
    project = _project(org)
    # Catalogue WITHOUT pr-review.
    mock_catalogue({"skills/something-else/SKILL.md": _skill_md("X", "x", "x")})

    result = _register(project, files=_agent_repo_tree())
    assert result.status == "ok"  # agent still registers
    [agent] = result.agents

    # The local skill resolved; the named one recorded a note.
    assert agent.created is True
    assert any("pr-review" in n for n in agent.skill_notes)
    assert any("not found in the built-in catalogue" in n for n in agent.skill_notes)

    # Only the local skill landed + only its AgentSkillRef.
    skills = {s.slug for s in Skill.objects.filter(organization=org, deleted_at__isnull=True)}
    assert skills == {"reviewer"}
    workload = Workload.objects.get(kind=Workload.Kind.AGENT, registered_app__organization=org)
    refs = {
        r.skill.slug: r.position
        for r in AgentSkillRef.objects.filter(workload=workload, deleted_at__isnull=True)
    }
    # Position reflects only the skills that actually resolved.
    assert refs == {"reviewer": 0}
    # The brief still assembled (it only depends on the README + resolved set).
    workload.refresh_from_db()
    assert workload.brief_id is not None


# ---------------------------------------------------------------------------
# Case 4: catalogue fetch outage is non-fatal (local skills still resolve)
# ---------------------------------------------------------------------------


def test_catalogue_fetch_outage_is_non_fatal(org, with_connection, mock_catalogue):
    with_connection(org)
    project = _project(org)
    mock_catalogue(None)  # catalogue fetch raises CatalogueFetchError

    result = _register(project, files=_agent_repo_tree())
    assert result.status == "ok"  # agent still registers despite the outage
    [agent] = result.agents
    assert any("catalogue unavailable" in n for n in agent.skill_notes)
    assert any("pr-review" in n for n in agent.skill_notes)

    # Local skill resolved; named one skipped.
    skills = {s.slug for s in Skill.objects.filter(organization=org, deleted_at__isnull=True)}
    assert skills == {"reviewer"}
    workload = Workload.objects.get(kind=Workload.Kind.AGENT, registered_app__organization=org)
    assert {
        r.skill.slug for r in AgentSkillRef.objects.filter(workload=workload, deleted_at__isnull=True)
    } == {"reviewer"}


def test_purely_local_skill_agent_never_fetches_catalogue(org, with_connection, mock_catalogue):
    """A repo whose agents reference only local skills must not hit the network."""
    with_connection(org)
    project = _project(org)
    calls = mock_catalogue(_catalogue_tree())

    toml_local_only = (
        'name = "triage"\n'
        "skills = [\n"
        '  { reviewer = "skills/reviewer" },\n'
        "]\n"
        "[[workloads]]\n"
        'name = "triage"\n'
        'kind = "agent"\n'
        "[[workloads.containers]]\n"
        'name = "triage"\n'
        "is_primary = true\n"
        'image_ref = "ecr.example/agent:latest"\n'
    )
    result = _register(project, files=_agent_repo_tree(toml_text=toml_local_only))
    assert result.status == "ok"
    # No named skill anywhere → the catalogue was never fetched.
    assert calls["n"] == 0
    assert {s.slug for s in Skill.objects.filter(organization=org, deleted_at__isnull=True)} == {"reviewer"}


# ---------------------------------------------------------------------------
# Case 5: tenancy — resolved skills/briefs land in the agent's own org
# ---------------------------------------------------------------------------


def test_resolved_skills_and_brief_are_org_scoped(org, other_org, with_connection, mock_catalogue):
    with_connection(org)
    with_connection(other_org)
    project_a = _project(org, slug="team-a")
    mock_catalogue(_catalogue_tree())

    res = _register(project_a, files=_agent_repo_tree(), repo="shared/agents")
    assert res.status == "ok"

    # Every Skill + Brief belongs to org A; org B has none.
    assert Skill.objects.filter(organization=org, deleted_at__isnull=True).count() == 2
    assert Skill.objects.filter(organization=other_org, deleted_at__isnull=True).count() == 0
    assert Brief.objects.filter(organization=org).count() == 1
    assert Brief.objects.filter(organization=other_org).count() == 0
    # No global skills were created (registration imports are always org-scoped).
    assert Skill.objects.filter(organization__isnull=True).count() == 0


# ---------------------------------------------------------------------------
# An agent that declares neither brief nor skills resolves cleanly (no-op)
# ---------------------------------------------------------------------------


def test_agent_without_brief_or_skills_is_noop(org, with_connection, mock_catalogue):
    with_connection(org)
    project = _project(org)
    calls = mock_catalogue(_catalogue_tree())

    bare = (
        'name = "bare"\n'
        "[[workloads]]\n"
        'name = "bare"\n'
        'kind = "agent"\n'
        "[[workloads.containers]]\n"
        'name = "bare"\n'
        "is_primary = true\n"
        'image_ref = "ecr.example/agent:latest"\n'
    )
    result = _register(project, files={"agents/bare/astrolift.toml": bare})
    assert result.status == "ok"
    [agent] = result.agents
    assert agent.skill_notes == []
    assert calls["n"] == 0  # no skills declared → no catalogue fetch
    assert Skill.objects.filter(organization=org).count() == 0
    assert Brief.objects.filter(organization=org).count() == 0
    workload = Workload.objects.get(kind=Workload.Kind.AGENT, registered_app__organization=org)
    assert workload.brief_id is None
    assert AgentSkillRef.objects.filter(workload=workload).count() == 0


# ---------------------------------------------------------------------------
# spec 39d — per-org skill repo refs ("<alias>/<skill-path>@<ref>")
# ---------------------------------------------------------------------------


def _agent_toml_with_org_repo_skill() -> str:
    """An agent manifest referencing a skill from a registered org repo."""
    return (
        'name = "triage"\n'
        "skills = [\n"
        '  { reviewer = "skills/reviewer" },\n'  # local
        '  "acme/dev-skills/security-review@v2",\n'  # org-repo
        "]\n"
        "[[workloads]]\n"
        'name = "triage"\n'
        'kind = "agent"\n'
        "[[workloads.containers]]\n"
        'name = "triage"\n'
        "is_primary = true\n"
        'image_ref = "ecr.example/agent:latest"\n'
    )


def _org_repo_tree() -> dict[str, str]:
    """A registered org skill repo's file tree carrying security-review."""
    return {
        "skills/dev-skills/security-review/SKILL.md": _skill_md(
            "Security Review",
            "Audit a change for security issues.",
            "Look for injection, authz, and secret-handling bugs.",
        ),
        "skills/dev-skills/security-review/scripts/scan.sh": "echo scan",
        "README.md": "org skills",
    }


@pytest.fixture
def mock_org_repo_fetch(monkeypatch):
    """Patch the org-repo tree fetch (no network).

    ``agent_skill_registration._resolve_org_repo_skill`` lazily imports
    ``fetch_org_repo_tree`` from the resolver module, so patching the resolver
    symbol exercises the real alias-lookup + caching path. Returns a ``calls``
    list recording each ``(repo_full_name, ref, is_private)`` fetch so a test
    can assert the per-pass cache fetched a repo exactly once."""

    def _install(tree, *, fail: bool = False):
        calls: list[tuple[str, str, bool]] = []

        def _fetch(repo, *, ref):
            calls.append((repo.repo_full_name, ref, repo.source_connection_id is not None))
            if fail:
                import requests

                raise requests.ConnectionError("org repo unreachable in test")
            return dict(tree)

        from astrolift_agents.services import skill_resolver

        monkeypatch.setattr(skill_resolver, "fetch_org_repo_tree", _fetch)
        return calls

    return _install


def test_org_repo_skill_resolves_via_registered_public_repo(
    org, with_connection, mock_catalogue, mock_org_repo_fetch
):
    with_connection(org)
    project = _project(org)
    mock_catalogue(_catalogue_tree())  # present but unused (no catalogue ref)
    calls = mock_org_repo_fetch(_org_repo_tree())

    # Register the org skill repo under alias "acme" (public — no connection).
    OrgSkillRepo.objects.create(
        organization=org, alias="acme", repo_full_name="acme/dev-skills", default_ref="main"
    )

    result = _register(project, files=_agent_repo_tree(toml_text=_agent_toml_with_org_repo_skill()))
    assert result.status == "ok"
    [agent] = result.agents
    assert agent.skill_notes == []  # both skills resolved cleanly

    # Local + org-repo skill both upserted in the org.
    skills = {s.slug: s for s in Skill.objects.filter(organization=org, deleted_at__isnull=True)}
    assert set(skills) == {"reviewer", "security-review"}
    assert skills["security-review"].name == "Security Review"
    assert skills["security-review"].content == "Look for injection, authz, and secret-handling bugs."

    # AgentSkillRef positions reflect manifest order (local first, org-repo second).
    workload = Workload.objects.get(kind=Workload.Kind.AGENT, registered_app__organization=org)
    refs = {
        r.skill.slug: r.position
        for r in AgentSkillRef.objects.filter(workload=workload, deleted_at__isnull=True)
    }
    assert refs == {"reviewer": 0, "security-review": 1}

    # Fetched once, at the manifest's @v2 pin, anonymously (public repo).
    assert calls == [("acme/dev-skills", "v2", False)]


def test_org_repo_skill_resolves_via_private_repo_connection(org, with_connection, mock_org_repo_fetch):
    with_connection(org)
    project = _project(org)
    calls = mock_org_repo_fetch(_org_repo_tree())

    # A private repo: link the org's SourceConnection.
    conn = SourceConnection.objects.create(
        organization=org,
        kind=SourceConnection.Kind.GITHUB_PAT,
        display_name="skills PAT",
        account_login="acme-skills",
    )
    OrgSkillRepo.objects.create(
        organization=org,
        alias="acme",
        repo_full_name="acme/private-skills",
        default_ref="main",
        source_connection=conn,
    )

    # Manifest ref without a pin → the repo's default_ref ("main") is used.
    toml_no_pin = _agent_toml_with_org_repo_skill().replace(
        "acme/dev-skills/security-review@v2", "acme/dev-skills/security-review"
    )
    result = _register(project, files=_agent_repo_tree(toml_text=toml_no_pin))
    assert result.status == "ok"
    [agent] = result.agents
    assert agent.skill_notes == []

    assert Skill.objects.filter(organization=org, slug="security-review", deleted_at__isnull=True).exists()
    # Fetched at the repo default_ref, via the linked connection (private=True).
    assert calls == [("acme/private-skills", "main", True)]


def test_org_repo_unknown_alias_is_non_fatal(org, with_connection, mock_org_repo_fetch):
    with_connection(org)
    project = _project(org)
    calls = mock_org_repo_fetch(_org_repo_tree())
    # NO OrgSkillRepo registered for alias "acme".

    result = _register(project, files=_agent_repo_tree(toml_text=_agent_toml_with_org_repo_skill()))
    assert result.status == "ok"  # agent still registers
    [agent] = result.agents
    # The org-repo skill recorded a note; no fetch was attempted (alias missing).
    assert any("acme" in n and "no skill repo registered" in n for n in agent.skill_notes)
    assert calls == []

    # Only the local skill landed.
    assert {s.slug for s in Skill.objects.filter(organization=org, deleted_at__isnull=True)} == {"reviewer"}
    workload = Workload.objects.get(kind=Workload.Kind.AGENT, registered_app__organization=org)
    assert {
        r.skill.slug for r in AgentSkillRef.objects.filter(workload=workload, deleted_at__isnull=True)
    } == {"reviewer"}


def test_org_repo_fetch_failure_is_non_fatal(org, with_connection, mock_org_repo_fetch):
    with_connection(org)
    project = _project(org)
    calls = mock_org_repo_fetch(_org_repo_tree(), fail=True)  # fetch raises
    OrgSkillRepo.objects.create(
        organization=org, alias="acme", repo_full_name="acme/dev-skills", default_ref="main"
    )

    result = _register(project, files=_agent_repo_tree(toml_text=_agent_toml_with_org_repo_skill()))
    assert result.status == "ok"  # agent still registers despite the outage
    [agent] = result.agents
    assert any("fetch failed" in n for n in agent.skill_notes)
    # The fetch was attempted once (then cached as failed for the pass).
    assert calls == [("acme/dev-skills", "v2", False)]

    # Local skill still resolved.
    assert {s.slug for s in Skill.objects.filter(organization=org, deleted_at__isnull=True)} == {"reviewer"}


def test_org_repo_skill_not_in_repo_is_non_fatal(org, with_connection, mock_org_repo_fetch):
    with_connection(org)
    project = _project(org)
    # Repo tree present but WITHOUT the requested skill folder.
    calls = mock_org_repo_fetch({"skills/dev-skills/other/SKILL.md": _skill_md("Other", "x", "x")})
    OrgSkillRepo.objects.create(
        organization=org, alias="acme", repo_full_name="acme/dev-skills", default_ref="main"
    )

    result = _register(project, files=_agent_repo_tree(toml_text=_agent_toml_with_org_repo_skill()))
    assert result.status == "ok"
    [agent] = result.agents
    assert any("security-review" in n and "not found in repo" in n for n in agent.skill_notes)
    assert calls == [("acme/dev-skills", "v2", False)]
    assert {s.slug for s in Skill.objects.filter(organization=org, deleted_at__isnull=True)} == {"reviewer"}


def test_org_repo_tree_cached_across_agents_in_one_pass(org, with_connection, mock_org_repo_fetch):
    """Two agents referencing the same org repo fetch it ONCE per pass."""
    with_connection(org)
    project = _project(org)
    calls = mock_org_repo_fetch(_org_repo_tree())
    OrgSkillRepo.objects.create(
        organization=org, alias="acme", repo_full_name="acme/dev-skills", default_ref="main"
    )

    agent_toml = _agent_toml_with_org_repo_skill()
    files = {
        "agents/triage/astrolift.toml": agent_toml,
        "agents/triage2/astrolift.toml": agent_toml.replace('name = "triage"', 'name = "triage2"'),
        "skills/reviewer/SKILL.md": _skill_md("Reviewer", "Reviews code.", "Read the diff."),
    }
    result = _register(project, files=files)
    assert result.status == "ok"
    assert len(result.agents) == 2
    assert all(a.skill_notes == [] for a in result.agents)
    # Same alias@v2 across both agents → exactly one fetch (per-pass cache).
    assert calls == [("acme/dev-skills", "v2", False)]


def test_org_repo_skill_is_org_scoped(org, other_org, with_connection, mock_org_repo_fetch):
    """A repo registered in org A is invisible to org B: B's agent gets a note,
    not A's repo."""
    with_connection(org)
    with_connection(other_org)
    project_b = _project(other_org, slug="team-b")
    calls = mock_org_repo_fetch(_org_repo_tree())
    # Register the alias in org A only.
    OrgSkillRepo.objects.create(
        organization=org, alias="acme", repo_full_name="acme/dev-skills", default_ref="main"
    )

    # Org B registers an agent referencing alias "acme" — unknown in B.
    res = _register(
        project_b, files=_agent_repo_tree(toml_text=_agent_toml_with_org_repo_skill()), repo="b/agents"
    )
    assert res.status == "ok"
    [agent] = res.agents
    assert any("no skill repo registered" in n for n in agent.skill_notes)
    assert calls == []  # B's alias lookup missed → no fetch
    # No org-repo skill leaked into B.
    assert not Skill.objects.filter(organization=other_org, slug="security-review").exists()
