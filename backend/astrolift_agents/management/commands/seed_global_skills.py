"""
``manage.py seed_global_skills``

Seeds the platform-global :class:`~astrolift_agents.models.skill.Skill`
catalog — the ready-made agent personas every org can pull into a Brief
without authoring their own (code review, infra audit, data QA, ...).

Each row is ``is_global=True`` with ``organization=None`` so it's
readable by all orgs and writable by platform admins only. The
``content`` is a short system prompt; ``agent_type`` pins the runtime
(claude / codex) and ``scaffolding_tags`` drive how the operator UI
surfaces the skill in its category palette.

Idempotent / upsert-style: safe to run on every container start. Matches
existing rows on ``(organization=None, slug)`` and updates them in
place; ``skill_version`` is bumped only when the content changes so an
unchanged re-run doesn't churn the version counter.
"""

from __future__ import annotations

import hashlib

from django.core.management.base import BaseCommand
from django.db import transaction

from astrolift_agents.models import Skill

# slug -> (name, agent_type, [scaffolding_tags], content)
GLOBAL_SKILLS: dict[str, tuple[str, str, list[str], str]] = {
    "code-review-fix": (
        "Code Review & Fix",
        "claude",
        ["code-review"],
        "You are a code reviewer. Find bugs and security issues. Report findings as JSON.",
    ),
    "infra-audit": (
        "Infrastructure Audit",
        "claude",
        ["infra-ops"],
        "You are an infrastructure auditor. Check K8s resources for security misconfigurations.",
    ),
    "data-pipeline-qa": (
        "Data Pipeline QA",
        "codex",
        ["data"],
        "You are a data pipeline QA agent. Validate data quality and pipeline correctness.",
    ),
    "pr-description-writer": (
        "PR Description Writer",
        "claude",
        ["productivity"],
        "You are a PR description writer. Summarize changes clearly for reviewers.",
    ),
    "workflow-supervisor": (
        "Workflow Supervisor",
        "claude",
        ["supervisor"],
        "You are a workflow supervisor. Monitor step results and decide: " "retry, skip, escalate, or abort.",
    ),
}


class Command(BaseCommand):
    help = "Upsert the platform-global Skill catalog (is_global, org=None)."

    @transaction.atomic
    def handle(self, *args, **options) -> None:
        created = 0
        updated = 0
        for slug, (name, agent_type, tags, content) in GLOBAL_SKILLS.items():
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

            skill = Skill.objects.filter(organization__isnull=True, slug=slug).first()
            is_new = skill is None
            if skill is None:
                skill = Skill(organization=None, slug=slug)
                skill.skill_version = 1
            elif skill.content_hash != content_hash:
                skill.skill_version = (skill.skill_version or 0) + 1

            skill.name = name
            skill.description = content
            skill.content = content
            skill.content_hash = content_hash
            skill.dependencies = []
            skill.agent_type = agent_type
            skill.scaffolding_tags = list(tags)
            skill.is_global = True
            skill.is_active = True
            skill.save()
            if is_new:
                created += 1
            else:
                updated += 1

        self.stdout.write(
            self.style.SUCCESS(f"seed_global_skills: {created} skill(s) created, {updated} updated.")
        )
