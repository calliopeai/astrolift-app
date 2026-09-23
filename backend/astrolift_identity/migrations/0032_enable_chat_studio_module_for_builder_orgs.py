# The builder API now requires the chat_studio_integration module (#1859),
# which is off until an org admin turns it on. Orgs that already have a dev
# environment were using that API before the gate existed, so they keep it.

from django.db import migrations

KEY = "chat_studio_integration"


def enable_for_existing_builder_orgs(apps, schema_editor):
    DevEnvironment = apps.get_model("astrolift_lifecycle", "DevEnvironment")
    OrganizationModule = apps.get_model("astrolift_identity", "OrganizationModule")
    org_ids = set(
        DevEnvironment.objects.filter(deleted_at__isnull=True).values_list("organization_id", flat=True)
    )
    already = set(
        OrganizationModule.objects.filter(
            key=KEY, deleted_at__isnull=True, organization_id__in=org_ids
        ).values_list("organization_id", flat=True)
    )
    OrganizationModule.objects.bulk_create(
        [OrganizationModule(organization_id=org_id, key=KEY, enabled=True) for org_id in sorted(org_ids - already)]
    )


class Migration(migrations.Migration):

    dependencies = [
        ("astrolift_identity", "0031_organizationmodule"),
        ("astrolift_lifecycle", "0036_deployment_build_error"),
    ]

    operations = [
        migrations.RunPython(enable_for_existing_builder_orgs, migrations.RunPython.noop),
    ]
