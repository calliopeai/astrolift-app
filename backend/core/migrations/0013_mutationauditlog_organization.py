"""Record which org's session wrote each mutation audit row (#1955).

``astroliftClusterLifecycleAudit`` could only tell rows apart by their
variables, so a shared cluster or a common slug returned other orgs' rows.

No backfill. A legacy row names no org, and its variables cannot prove
one: a failed attempt from another org carries the same cluster guid as
the owner's own mutation. A guess is exactly the leak this closes, so
legacy rows stay NULL; no tenant view shows them, and superusers still
read them through ``auditLogs``.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('astrolift_identity', '0033_resync_system_roles_zentinelle'),
        ('core', '0012_alter_resourcefile_file'),
    ]

    operations = [
        migrations.AddField(
            model_name='mutationauditlog',
            name='organization',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='mutation_audit_logs', to='astrolift_identity.organization'),
        ),
    ]
