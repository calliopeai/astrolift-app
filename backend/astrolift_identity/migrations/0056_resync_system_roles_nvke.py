"""Give existing organization owners/admins the explicit NVKE permissions."""

from django.db import migrations


def update_roles(apps, schema_editor):
    Role = apps.get_model("astrolift_identity", "Role")
    for slug in ("org_owner", "org_admin"):
        for role in Role.objects.filter(slug=slug, is_system=True, organization=None, deleted_at__isnull=True):
            permissions = list(role.permissions)
            for permission in ("nvke.read", "nvke.manage", "nvke.admin"):
                if permission not in permissions:
                    permissions.append(permission)
            Role.objects.filter(pk=role.pk).update(permissions=permissions)


class Migration(migrations.Migration):
    dependencies = [("astrolift_identity", "0055_keep_chat_studio_integration_allowed")]
    operations = [migrations.RunPython(update_roles, migrations.RunPython.noop)]
