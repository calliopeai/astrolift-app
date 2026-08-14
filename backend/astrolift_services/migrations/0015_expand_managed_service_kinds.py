from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_services", "0014_managedserviceattachment_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="managedservice",
            name="kind",
            field=models.CharField(
                choices=[
                    ("postgres", "Postgres"),
                    ("mysql", "Mysql"),
                    ("mssql", "Mssql"),
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
                    ("faas", "Faas"),
                    ("api_gateway", "Api Gateway"),
                    ("event_stream", "Event Stream"),
                    ("filesystem", "Filesystem"),
                    ("sms", "Sms"),
                    ("database_proxy", "Database Proxy"),
                    ("graph_db", "Graph Db"),
                    ("wide_column", "Wide Column"),
                    ("warehouse", "Warehouse"),
                    ("event_bus", "Event Bus"),
                    ("stream", "Stream"),
                    ("workflow_engine", "Workflow Engine"),
                    ("encryption_key", "Encryption Key"),
                    ("private_endpoint", "Private Endpoint"),
                    ("observability", "Observability"),
                ],
                max_length=32,
            ),
        ),
    ]
