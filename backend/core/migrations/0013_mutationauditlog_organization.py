"""Record which org's session wrote each mutation audit row (#1955).

``astroliftClusterLifecycleAudit`` could only tell rows apart by their
variables, so a shared cluster or a common slug returned other orgs' rows.

No backfill. A legacy row names no org, and its variables cannot prove
one: a failed attempt from another org carries the same cluster guid as
the owner's own mutation. A guess is exactly the leak this closes, so
legacy rows stay NULL; no tenant view shows them, and superusers still
read them through ``auditLogs``.

The column carries no index here. 0014 builds its index concurrently: this
migration's transaction holds ACCESS EXCLUSIVE on the table until it
commits, and a plain index build on a large, never-pruned log would keep
every mutation's audit insert (and every read) waiting for its duration.
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
            field=models.ForeignKey(blank=True, db_index=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='mutation_audit_logs', to='astrolift_identity.organization'),
        ),
    ]
