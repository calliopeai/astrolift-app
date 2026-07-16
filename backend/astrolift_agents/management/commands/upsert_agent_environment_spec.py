"""
``manage.py upsert_agent_environment_spec`` — create or update one
org-scoped :class:`~astrolift_agents.models.agent_environment_spec.AgentEnvironmentSpec`.

A generic admin tool for wiring an agent's dispatch config (image, the
config repo + manifest path its Brief is assembled from, secret refs,
env) without going through the GraphQL API. Idempotent: matches on
``(organization, slug)`` among non-deleted rows and updates in place.

Example (run on a live container via smd-opscode
``scripts/astrolift-debug.sh manage``)::

    manage.py upsert_agent_environment_spec \\
        --org steadymd \\
        --slug emr-bug-triage \\
        --name "EMR Bug Triage" \\
        --agent-type claude \\
        --image-tag 464386617157.dkr.ecr.us-west-2.amazonaws.com/astrolift/agent-claude:latest \\
        --config-repo steadymd/smd-agents \\
        --manifest-path agents/emr-bug-triage/astrolift.toml \\
        --env EMR_SERVICE_BASE_URL=https://emr.prd.smdinfra.net \\
        --secret EMR_AGENT_TOKEN=smd-emr-agent-token
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from astrolift_agents.models import AgentEnvironmentSpec
from astrolift_identity.models import Organization


def _parse_pairs(values: list[str] | None, *, what: str) -> list[tuple[str, str]]:
    pairs = []
    for raw in values or []:
        if "=" not in raw:
            raise CommandError(f"--{what} must be KEY=VALUE, got {raw!r}")
        key, val = raw.split("=", 1)
        key = key.strip()
        if not key:
            raise CommandError(f"--{what} has an empty key: {raw!r}")
        pairs.append((key, val))
    return pairs


class Command(BaseCommand):
    help = "Create or update an org-scoped AgentEnvironmentSpec (idempotent on org+slug)."

    def add_arguments(self, parser):
        parser.add_argument("--org", required=True, help="Organization slug or guid.")
        parser.add_argument("--slug", required=True, help="Spec slug (lookup key).")
        parser.add_argument("--name", default="", help="Display name (defaults to slug).")
        parser.add_argument("--agent-type", required=True, choices=AgentEnvironmentSpec.AgentType.values)
        parser.add_argument("--image-tag", default="", help="Explicit image URI (wins over --runtime).")
        parser.add_argument(
            "--runtime", default="", help="Catalog runtime short-name (used only if --image-tag is blank)."
        )
        parser.add_argument("--tool-preset", default="")
        parser.add_argument("--config-repo", default="", help='"owner/repo" of the config repo.')
        parser.add_argument("--config-branch", default="main")
        parser.add_argument(
            "--manifest-path",
            default="",
            help="Repo-relative path to this agent's astrolift.toml (or its dir).",
        )
        parser.add_argument("--allow-install", action="store_true")
        parser.add_argument("--vnc", action="store_true")
        parser.add_argument(
            "--env", action="append", metavar="KEY=VALUE", help="Non-secret env var (repeatable)."
        )
        parser.add_argument(
            "--secret",
            action="append",
            metavar="ENV_VAR=SECRET_NAME",
            help="Secret reference: env var <- secret store name/URI (repeatable).",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        org = Organization.objects.filter(slug=options["org"], deleted_at__isnull=True).first()
        if org is None:
            # Fall back to guid; a non-UUID string just means "not found".
            try:
                org = Organization.objects.filter(guid=options["org"], deleted_at__isnull=True).first()
            except (ValidationError, ValueError):
                org = None
        if org is None:
            raise CommandError(f"organization not found: {options['org']!r}")

        env_vars = dict(_parse_pairs(options.get("env"), what="env"))
        secret_refs = [
            {"env_var": env_var, "uri": uri}
            for env_var, uri in _parse_pairs(options.get("secret"), what="secret")
        ]

        slug = options["slug"]
        spec = AgentEnvironmentSpec.objects.filter(
            organization=org, slug=slug, deleted_at__isnull=True
        ).first()
        is_new = spec is None
        if spec is None:
            spec = AgentEnvironmentSpec(organization=org, slug=slug)

        spec.name = options["name"].strip() or slug
        spec.agent_type = options["agent_type"]
        spec.image_tag = options["image_tag"].strip()
        spec.runtime = options["runtime"].strip()
        spec.tool_preset = options["tool_preset"].strip()
        spec.config_repo = options["config_repo"].strip()
        spec.config_branch = (options["config_branch"] or "main").strip()
        spec.config_manifest_path = options["manifest_path"].strip()
        spec.allow_install = bool(options["allow_install"])
        spec.vnc_enabled = bool(options["vnc"])
        spec.env_vars = env_vars
        spec.secret_refs = secret_refs
        spec.save()

        verb = "Created" if is_new else "Updated"
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb} AgentEnvironmentSpec {org.slug}/{slug} "
                f"(agent_type={spec.agent_type}, image_tag={spec.image_tag or '-'}, "
                f"config={spec.config_repo}@{spec.config_branch}:{spec.config_manifest_path or '/'})"
            )
        )
