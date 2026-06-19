"""
OrgSkillRepo — a per-org registered skill repo (spec 39 §Per-org skill repos,
build phase 39d).

An org registers one or more skill repos (agentskills.io-format folders),
each under a short ``alias``. A manifest then references a skill in that repo
GitHub-Actions-style — ``"<alias>/<skill-path>@<ref>"`` (e.g.
``"acme/dev-skills/pr-review@v2"``) — and the skill resolver fetches the repo's
tree at ``ref`` (or the repo's ``default_ref``), locates the skill folder, and
loads it alongside the built-in catalogue + the agent's local skills.

Mechanism mirrors how source-providers / clusters are registered: a model + a
mutation + RBAC (``scm.connect`` — the same grant source registration uses).

Public vs private fetch
-----------------------
``source_connection`` is an optional FK to a :class:`SourceConnection`:

* **null** — the repo is public; the resolver fetches it anonymously via
  ``fetch_public_repo_tree`` (the same no-auth path the built-in catalogue
  uses), so an org can register a public skill repo with zero credentials.
* **set** — the repo is private; the resolver fetches it through that
  connection's credential via ``fetch_repo_tree``. The connection is the
  org's existing SCM credential, so registering a private skill repo reuses
  the source-connection the org already has.
"""

from __future__ import annotations

from django.db import models

from core.models.base import BaseCoreModel


class OrgSkillRepo(BaseCoreModel):
    """A skill repo an org registered under an alias (spec 39d)."""

    organization = models.ForeignKey(
        "astrolift_identity.Organization",
        related_name="org_skill_repos",
        on_delete=models.CASCADE,
    )
    # The short handle a manifest references: "<alias>/<skill-path>@<ref>".
    # Unique per org (the partial constraint below scopes uniqueness to live
    # rows so a removed alias can be re-claimed).
    alias = models.CharField(
        max_length=128,
        help_text="Short handle a manifest references as '<alias>/<skill-path>@<ref>'.",
    )
    # "owner/repo" on the source host (e.g. "acme/dev-skills").
    repo_full_name = models.CharField(
        max_length=512,
        help_text="owner/repo on the source host, e.g. 'acme/dev-skills'.",
    )
    # The source host. Matches the host-prefix half of a SourceConnection.Kind
    # ("github" / "gitlab" / "bitbucket" / "gitea"); the resolver routes the
    # fetch by this when a connection is attached, and the public fetch path
    # supports GitHub today.
    source_kind = models.CharField(
        max_length=32,
        default="github",
        help_text="Source host: github / gitlab / bitbucket / gitea.",
    )
    # The branch/tag/sha fetched when a manifest ref carries no '@' pin.
    default_ref = models.CharField(max_length=255, default="main")
    # Optional credential for a PRIVATE repo. NULL → public (anonymous fetch).
    # SET_NULL (not CASCADE): deleting the connection should orphan-to-public
    # rather than silently delete the registered skill repo, so the operator
    # sees a fetch failure (a clear note) and can re-attach a connection.
    source_connection = models.ForeignKey(
        "astrolift_scm.SourceConnection",
        related_name="org_skill_repos",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    # Operator-friendly label shown in the UI; falls back to the alias.
    display_name = models.CharField(max_length=200, blank=True, default="")
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "alias"],
                condition=models.Q(deleted_at__isnull=True),
                name="org_skill_repo_alias_unique_active_per_org",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "is_active"], name="org_skill_repo_org_active_idx"),
        ]

    @property
    def is_private(self) -> bool:
        """True when this repo fetches through a credential (vs anonymously)."""
        return self.source_connection_id is not None

    @property
    def name(self) -> str:
        return self.display_name or self.alias

    def __str__(self) -> str:
        return f"OrgSkillRepo {self.alias} -> {self.repo_full_name}"
