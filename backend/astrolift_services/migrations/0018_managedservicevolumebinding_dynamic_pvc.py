from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_services", "0017_managedservicevolumebinding"),
    ]

    operations = [
        migrations.AddField(
            model_name="managedservicevolumebinding",
            name="storage_class_name",
            field=models.CharField(blank=True, default="", max_length=253),
        ),
        migrations.AlterField(
            model_name="managedservicevolumebinding",
            name="source_kind",
            field=models.CharField(
                choices=[
                    ("existing_pvc", "Existing Pvc"),
                    ("csi", "Csi"),
                    ("dynamic_pvc", "Dynamic Pvc"),
                ],
                max_length=32,
            ),
        ),
        migrations.RemoveConstraint(
            model_name="managedservicevolumebinding",
            name="msvc_volume_source_fields_valid",
        ),
        migrations.AddConstraint(
            model_name="managedservicevolumebinding",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        source_kind="existing_pvc",
                        claim_name__gt="",
                        claim_namespace__gt="",
                        storage_class_name="",
                        csi_driver="",
                        volume_handle="",
                    )
                    | models.Q(
                        source_kind="csi",
                        csi_driver__gt="",
                        volume_handle__gt="",
                        claim_name="",
                        claim_namespace="",
                        storage_class_name="",
                    )
                    | models.Q(
                        source_kind="dynamic_pvc",
                        storage_class_name__gt="",
                        claim_name="",
                        claim_namespace="",
                        volume_handle="",
                    )
                ),
                name="msvc_volume_source_fields_valid",
            ),
        ),
    ]
