import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import core.fields.uuid_v7


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_services", "0016_managedservice_kind_cache"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ManagedServiceVolumeBinding",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("version", models.IntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("deleted_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                (
                    "guid",
                    core.fields.uuid_v7.UUIDv7Field(
                        db_index=True,
                        default=core.fields.uuid_v7.uuid7,
                        editable=False,
                        unique=True,
                    ),
                ),
                ("name", models.CharField(max_length=63)),
                ("mount_path", models.CharField(max_length=512)),
                ("sub_path", models.CharField(blank=True, default="", max_length=512)),
                (
                    "source_kind",
                    models.CharField(
                        choices=[("existing_pvc", "Existing Pvc"), ("csi", "Csi")],
                        max_length=32,
                    ),
                ),
                ("protocol", models.CharField(max_length=32)),
                ("claim_name", models.CharField(blank=True, default="", max_length=253)),
                ("claim_namespace", models.CharField(blank=True, default="", max_length=253)),
                ("csi_driver", models.CharField(blank=True, default="", max_length=253)),
                ("volume_handle", models.CharField(blank=True, default="", max_length=1024)),
                ("volume_attributes", models.JSONField(blank=True, default=dict)),
                ("secret_refs", models.JSONField(blank=True, default=dict)),
                ("secret_literals", models.JSONField(blank=True, default=dict)),
                ("mount_options", models.JSONField(blank=True, default=list)),
                ("read_only", models.BooleanField(default=False)),
                ("capacity", models.CharField(default="1Gi", max_length=32)),
                ("access_modes", models.JSONField(blank=True, default=list)),
                ("workload_names", models.JSONField(blank=True, default=list)),
                ("container_names", models.JSONField(blank=True, default=list)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Creator",
                    ),
                ),
                (
                    "deleted_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "managed_service",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="volume_bindings",
                        to="astrolift_services.managedservice",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="managedservicevolumebinding",
            constraint=models.UniqueConstraint(
                condition=models.Q(deleted_at__isnull=True),
                fields=("managed_service", "name"),
                name="msvc_volume_name_unique_active",
            ),
        ),
        migrations.AddConstraint(
            model_name="managedservicevolumebinding",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        claim_name__gt="",
                        claim_namespace__gt="",
                        csi_driver="",
                        source_kind="existing_pvc",
                        volume_handle="",
                    )
                    | models.Q(
                        claim_name="",
                        claim_namespace="",
                        csi_driver__gt="",
                        source_kind="csi",
                        volume_handle__gt="",
                    )
                ),
                name="msvc_volume_source_fields_valid",
            ),
        ),
    ]
