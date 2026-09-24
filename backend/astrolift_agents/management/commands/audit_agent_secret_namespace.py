"""
``manage.py audit_agent_secret_namespace``: list every stored secret location
that stops resolving once secret refs are confined to the owning org's
namespace (#1921).

Read-only. It reads control-plane rows and never touches a secret store, so it
is safe to run against production before deploying the release that enforces
the namespace. Every finding is one tab-separated line: org slug, org guid,
what holds the location, its identifier, the field, and the reason.

    manage.py audit_agent_secret_namespace
    manage.py audit_agent_secret_namespace --org steadymd

Move each value it lists under ``agents/<org guid>/`` (typed agent refs),
``agent-bundles/<org guid>/`` (org secret bundles) or
``services/<org guid>/<owner guid>/`` (managed-service config, where the owner is
the service's app, or its project for a project service), point the ref there,
and run it again until it prints nothing. A Google Secret Manager reference in a
GCP service's config (Cloud Functions ``secret_environment``/``secret_volumes``,
Managed Kafka Connect ``secret_paths``) must name, in the install project, the
secret id the GCP secrets driver gives that location.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from astrolift_agents.models import AgentEnvironmentSpec
from astrolift_dispatch.agent_secrets import unscoped_bundle_reason, unscoped_secret_refs
from astrolift_identity.models import Organization
from astrolift_services.models import ManagedService, SecretBundle
from astrolift_services.secret_ref_config import (
    managed_binding_ref_reason,
    service_organization,
    service_owner,
    unscoped_config_secret_refs,
)


class Command(BaseCommand):
    help = "List stored secret locations outside their org's secret namespace (#1921). Read-only."

    def add_arguments(self, parser):
        parser.add_argument("--org", default="", help="Only this organization (slug or guid).")

    def handle(self, *args, **options):
        org = self._organization(options["org"]) if options["org"] else None
        findings = [*self._specs(org), *self._bundles(org), *self._services(org)]
        for row in findings:
            self.stdout.write("\t".join(row))
        self.stdout.write(f"{len(findings)} location(s) outside the org secret namespace")

    def _organization(self, value: str) -> Organization:
        organization = Organization.objects.filter(slug=value, deleted_at__isnull=True).first()
        if organization is None:
            try:
                organization = Organization.objects.filter(guid=value, deleted_at__isnull=True).first()
            except (ValidationError, ValueError):
                organization = None
        if organization is None:
            raise CommandError(f"organization not found: {value!r}")
        return organization

    def _specs(self, org):
        specs = AgentEnvironmentSpec.objects.filter(deleted_at__isnull=True).select_related("organization")
        if org is not None:
            specs = specs.filter(organization=org)
        for spec in specs.order_by("organization__slug", "slug"):
            for env_var, reason in sorted(unscoped_secret_refs(spec).items()):
                yield _row(spec.organization, "agent env spec", spec.slug, env_var, reason)

    def _bundles(self, org):
        bundles = SecretBundle.objects.filter(deleted_at__isnull=True).select_related("organization")
        if org is not None:
            bundles = bundles.filter(organization=org)
        for bundle in bundles.order_by("organization__slug", "slug"):
            reason = unscoped_bundle_reason(bundle, organization=bundle.organization)
            if reason is not None:
                yield _row(bundle.organization, "secret bundle", bundle.slug, "backendRef", reason)

    def _services(self, org):
        services = ManagedService.objects.filter(deleted_at__isnull=True).select_related(
            "registered_app__organization",
            "project__organization",
            "tenant_cluster__provider_plugin",
            "app_environment__tenant_cluster__provider_plugin",
        )
        if org is not None:
            services = services.filter(Q(registered_app__organization=org) | Q(project__organization=org))
        for svc in services.order_by("kind", "name", "pk"):
            organization = service_organization(svc)
            where = f"{svc.kind}/{svc.name or svc.kind} ({svc.guid})"
            findings: dict[str, str] = {}
            owner, cluster = service_owner(svc), svc.effective_cluster
            for config in (svc.config, svc.applied_config):
                for path, _ref, reason in unscoped_config_secret_refs(config, owner=owner, cluster=cluster):
                    findings.setdefault(f"config.{path}", reason)
            if not findings:
                # A driver may also copy a string from a field no check names
                # into a row; the row check recognises the copy.
                rows = [
                    (f"binding {row.env_key}", row.env_value_ref)
                    for row in svc.bindings.filter(deleted_at__isnull=True, is_secret=True).order_by(
                        "env_key"
                    )
                ] + [
                    (f"volume {row.name} {key}", str(ref))
                    for row in svc.volume_bindings.filter(deleted_at__isnull=True).order_by("name")
                    for key, ref in sorted((row.secret_refs or {}).items())
                ]
                for field, ref in rows:
                    reason = managed_binding_ref_reason(svc, ref)
                    if reason is not None:
                        findings.setdefault(field, reason)
            for field, reason in findings.items():
                yield _row(organization, "managed service", where, field, reason)


def _row(organization, holder: str, identifier: str, field: str, reason: str) -> tuple[str, ...]:
    return (organization.slug, str(organization.guid), holder, identifier, field, reason)
