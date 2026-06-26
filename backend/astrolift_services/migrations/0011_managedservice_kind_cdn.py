from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_services", "0010_managedservice_backend_ref"),
    ]

    operations = [
        migrations.AlterField(
            model_name="managedservice",
            name="kind",
            field=models.CharField(
                choices=[
                    ("postgres", "Postgres"),
                    ("redis", "Redis"),
                    ("object_store", "Object Store"),
                    ("queue", "Queue"),
                    ("topic", "Topic"),
                    ("kv_store", "Kv Store"),
                    ("search", "Search"),
                    ("mq", "Mq"),
                    ("nfs", "Nfs"),
                    ("vector_index", "Vector Index"),
                    ("time_series", "Time Series"),
                    ("document_db", "Document Db"),
                    ("email", "Email"),
                    ("model_endpoint", "Model Endpoint"),
                    ("cdn", "Cdn"),
                ],
                max_length=32,
            ),
        ),
    ]
