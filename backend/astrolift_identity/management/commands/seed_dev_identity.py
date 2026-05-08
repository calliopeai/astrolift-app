"""
``manage.py seed_dev_identity``

Creates a usable identity stack for local dev:

* Organization ``acme``
* Team ``acme/eng``
* Project ``acme/eng/api``
* User ``dev@local.astrolift.net`` (django superuser, password ``dev``)
* RoleBinding(user, org_owner, ORG=acme)
* OrgDomain(``local.astrolift.net``, jit_enabled, default_role=team_viewer)

Re-runnable: every step is upsert-style, so repeat invocations are
cheap and don't pile up duplicate rows.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from astrolift_identity.models import (
    Member,
    Organization,
    OrgDomain,
    Project,
    Role,
    RoleBinding,
    Team,
)


class Command(BaseCommand):
    help = "Seed a local-dev identity stack (org, team, project, user, bindings)."

    def add_arguments(self, parser):
        parser.add_argument("--email", default="dev@local.astrolift.net")
        parser.add_argument("--password", default="dev")
        # 'acme' was the first iteration's placeholder; obvious-placeholder
        # naming saves OSS users from a confused "why does my Astrolift say
        # Acme?" moment on first login. Production installs use
        # bootstrap_admin which requires --org-name explicitly.
        parser.add_argument("--org", default="local")
        parser.add_argument(
            "--org-name", default="Astrolift Local Dev"
        )
        parser.add_argument("--team", default="eng")
        parser.add_argument("--project", default="api")

    def handle(
        self,
        *args,
        email: str,
        password: str,
        org: str,
        org_name: str,
        team: str,
        project: str,
        **opts,
    ):
        org_row, _ = Organization.objects.get_or_create(
            slug=org,
            defaults={"name": org_name, "website": f"https://{org}.example"},
        )
        team_row, _ = Team.objects.get_or_create(
            organization=org_row,
            slug=team,
            defaults={"name": team.title()},
        )
        project_row, _ = Project.objects.get_or_create(
            team=team_row,
            slug=project,
            defaults={"name": project.title()},
        )

        User = get_user_model()
        user, created = User.objects.get_or_create(
            username=email,
            defaults={"email": email, "is_staff": True, "is_superuser": True},
        )
        # Always reset password so re-runs after a wipe still log in.
        user.set_password(password)
        user.is_staff = True
        user.is_superuser = True
        user.save()

        try:
            org_owner = Role.objects.get(slug="org_owner", is_system=True, organization=None)
        except Role.DoesNotExist:
            self.stderr.write(self.style.ERROR(
                "system role 'org_owner' missing — run migrate first"
            ))
            return

        RoleBinding.objects.update_or_create(
            user=user,
            role=org_owner,
            scope_kind="ORG",
            scope_id=org_row.id,
            defaults={},
        )

        # Member row at ORG scope so TenantContextMiddleware can
        # resolve the user's org via the single-membership rule.
        Member.objects.update_or_create(
            user=user,
            scope_kind="ORG",
            scope_id=org_row.id,
            defaults={"is_active": True, "lifecycle": "active"},
        )

        team_viewer = Role.objects.filter(
            slug="team_viewer", is_system=True, organization=None
        ).first()
        if team_viewer is not None:
            domain = email.split("@", 1)[1]
            OrgDomain.objects.update_or_create(
                domain=domain,
                defaults={
                    "organization": org_row,
                    "jit_enabled": True,
                    "default_role": team_viewer,
                    "default_team": team_row,
                },
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"seeded: org={org_row.slug} team={team_row.slug} "
                f"project={project_row.slug} user={user.username} "
                f"{'(created)' if created else '(updated)'}"
            )
        )
        self.stdout.write(
            f"  password: {password}\n"
            f"  org guid: {org_row.guid}\n"
            f"  user pk:  {user.pk}\n"
        )
