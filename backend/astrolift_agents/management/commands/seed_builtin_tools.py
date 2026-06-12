"""
``manage.py seed_builtin_tools``

Seeds the platform-standard :class:`~astrolift_agents.models.skill.ToolDef`
catalog — the shell tools that ship pre-installed in every agent runtime
image (git, kubectl, terraform, ...).

ToolDef rows hang off a parent Skill (the model FK is non-nullable), so
the built-ins attach to a single global host skill
(``builtin-tools``, ``is_global=True``, ``organization=None``) which is
the "is_global equivalent" for tools: a global skill is readable by
every org, so its tool defs are too. No tenant owns the built-in
catalog.

Each tool is grouped by ``capability_group`` (dev / cloud / k8s / data /
infra) and lists the shell ``commands`` it exposes. ``is_builtin=True``
marks them as pre-installed (vs. installed-on-demand), and the adapter
is ``python_fn`` — the dispatch layer shells out to these via the
runtime's built-in command bridge rather than an HTTP/MCP hop.

Idempotent / upsert-style: safe to run on every container start. Matches
existing rows on ``(skill, slug)`` and updates them in place.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from astrolift_agents.models import Skill, ToolDef

# slug -> (capability_group, [commands]). The slug doubles as the tool
# name; commands are the executables the tool exposes inside the agent
# sandbox.
BUILTIN_TOOLS: dict[str, tuple[str, list[str]]] = {
    "dev-toolkit": ("dev", ["git", "gh", "make", "curl", "jq", "yq", "ripgrep"]),
    "cloud-aws": ("cloud", ["aws"]),
    "k8s-toolkit": ("k8s", ["kubectl", "helm"]),
    "data-clients": ("data", ["psql", "redis-cli", "sqlite3"]),
    "infra-terraform": ("infra", ["terraform"]),
}

_HOST_SKILL_SLUG = "builtin-tools"


class Command(BaseCommand):
    help = "Upsert the platform-standard built-in ToolDef catalog (global)."

    @transaction.atomic
    def handle(self, *args, **options) -> None:
        host = self._upsert_host_skill()

        created = 0
        updated = 0
        for slug, (group, commands) in BUILTIN_TOOLS.items():
            tool = ToolDef.objects.filter(skill=host, slug=slug).first()
            is_new = tool is None
            if tool is None:
                tool = ToolDef(skill=host, slug=slug)
            tool.name = slug
            tool.description = f"Built-in {group} tools: {', '.join(commands)}."
            tool.adapter = ToolDef.Adapter.PYTHON_FN
            tool.capability_group = group
            tool.commands = list(commands)
            tool.required_packages = []
            # Built-ins bind to every agent runtime.
            tool.agent_type_bindings = ["*"]
            tool.is_builtin = True
            tool.save()
            if is_new:
                created += 1
            else:
                updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"seed_builtin_tools: host skill {host.slug!r}; "
                f"{created} tool(s) created, {updated} updated."
            )
        )

    def _upsert_host_skill(self) -> Skill:
        """The global skill that owns the built-in tool defs.

        Global skills are keyed on ``(organization, slug)`` with
        ``organization=None``; the partial unique index only covers live
        rows, so a re-run reuses the existing row.
        """
        skill = Skill.objects.filter(organization__isnull=True, slug=_HOST_SKILL_SLUG).first()
        if skill is None:
            skill = Skill(organization=None, slug=_HOST_SKILL_SLUG)
        skill.name = "Built-in Tools"
        skill.description = (
            "Platform-standard tools pre-installed in every agent runtime image. "
            "Host skill for the global ToolDef catalog."
        )
        skill.is_global = True
        skill.is_active = True
        skill.agent_type = "any"
        skill.scaffolding_tags = ["builtin"]
        skill.save()
        return skill
