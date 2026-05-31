# Generated migration for astrolift_pipelines initial schema.

import core.fields.uuid_v7
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('astrolift_identity', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Pipeline',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('version', models.IntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('deleted_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('guid', core.fields.uuid_v7.UUIDv7Field(db_index=True, default=core.fields.uuid_v7.uuid7, editable=False, unique=True)),
                ('name', models.SlugField(max_length=200)),
                ('repo_url', models.CharField(blank=True, default='', max_length=512)),
                ('default_branch', models.CharField(default='main', max_length=128)),
                ('toml_path', models.CharField(blank=True, default='', max_length=512)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='Creator')),
                ('deleted_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('organization', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='pipelines', to='astrolift_identity.organization')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['organization', 'name'],
            },
        ),
        migrations.CreateModel(
            name='PipelineRun',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('version', models.IntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('deleted_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('guid', core.fields.uuid_v7.UUIDv7Field(db_index=True, default=core.fields.uuid_v7.uuid7, editable=False, unique=True)),
                ('run_number', models.PositiveIntegerField()),
                ('trigger_kind', models.CharField(choices=[('push', 'Push'), ('pull_request', 'Pull Request'), ('schedule', 'Schedule'), ('manual', 'Manual'), ('api', 'API')], max_length=32)),
                ('trigger_ref', models.CharField(blank=True, default='', max_length=255)),
                ('trigger_actor', models.CharField(blank=True, default='', max_length=255)),
                ('temporal_workflow_id', models.CharField(blank=True, default='', max_length=512)),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('running', 'Running'), ('success', 'Success'), ('failure', 'Failure'), ('cancelled', 'Cancelled')], default='pending', max_length=32)),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('finished_at', models.DateTimeField(blank=True, null=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='Creator')),
                ('deleted_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('pipeline', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='runs', to='astrolift_pipelines.pipeline')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['pipeline', '-run_number'],
            },
        ),
        migrations.CreateModel(
            name='Job',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('version', models.IntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('deleted_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('guid', core.fields.uuid_v7.UUIDv7Field(db_index=True, default=core.fields.uuid_v7.uuid7, editable=False, unique=True)),
                ('job_id', models.CharField(max_length=200)),
                ('name', models.CharField(max_length=200)),
                ('runs_on', models.CharField(blank=True, default='', max_length=200)),
                ('container_image', models.CharField(blank=True, default='', max_length=512)),
                ('needs', models.JSONField(blank=True, default=list)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='Creator')),
                ('deleted_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('pipeline', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='jobs', to='astrolift_pipelines.pipeline')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['pipeline', 'job_id'],
            },
        ),
        migrations.CreateModel(
            name='JobRun',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('version', models.IntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('deleted_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('guid', core.fields.uuid_v7.UUIDv7Field(db_index=True, default=core.fields.uuid_v7.uuid7, editable=False, unique=True)),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('running', 'Running'), ('success', 'Success'), ('failure', 'Failure'), ('skipped', 'Skipped'), ('cancelled', 'Cancelled')], default='pending', max_length=32)),
                ('temporal_activity_id', models.CharField(blank=True, default='', max_length=512)),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('finished_at', models.DateTimeField(blank=True, null=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='Creator')),
                ('deleted_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('job', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='job_runs', to='astrolift_pipelines.job')),
                ('pipeline_run', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='job_runs', to='astrolift_pipelines.pipelinerun')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['pipeline_run', 'job__job_id'],
            },
        ),
        migrations.CreateModel(
            name='Step',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('version', models.IntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('deleted_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('guid', core.fields.uuid_v7.UUIDv7Field(db_index=True, default=core.fields.uuid_v7.uuid7, editable=False, unique=True)),
                ('position', models.PositiveIntegerField()),
                ('step_id', models.CharField(blank=True, default='', max_length=200)),
                ('uses', models.CharField(blank=True, max_length=512, null=True)),
                ('run', models.TextField(blank=True, null=True)),
                ('env', models.JSONField(blank=True, default=dict)),
                ('with_params', models.JSONField(blank=True, default=dict)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='Creator')),
                ('deleted_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('job', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='steps', to='astrolift_pipelines.job')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['job', 'position'],
            },
        ),
        migrations.CreateModel(
            name='StepRun',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('version', models.IntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('deleted_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('guid', core.fields.uuid_v7.UUIDv7Field(db_index=True, default=core.fields.uuid_v7.uuid7, editable=False, unique=True)),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('running', 'Running'), ('success', 'Success'), ('failure', 'Failure'), ('skipped', 'Skipped'), ('cancelled', 'Cancelled')], default='pending', max_length=32)),
                ('exit_code', models.IntegerField(blank=True, null=True)),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('finished_at', models.DateTimeField(blank=True, null=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='Creator')),
                ('deleted_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('job_run', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='step_runs', to='astrolift_pipelines.jobrun')),
                ('step', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='step_runs', to='astrolift_pipelines.step')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['job_run', 'step__position'],
            },
        ),
        migrations.CreateModel(
            name='Artifact',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('version', models.IntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('deleted_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('guid', core.fields.uuid_v7.UUIDv7Field(db_index=True, default=core.fields.uuid_v7.uuid7, editable=False, unique=True)),
                ('name', models.CharField(max_length=255)),
                ('blob_key', models.CharField(max_length=1024)),
                ('size_bytes', models.BigIntegerField(default=0)),
                ('content_type', models.CharField(blank=True, default='', max_length=255)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='Creator')),
                ('deleted_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('job_run', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='artifacts', to='astrolift_pipelines.jobrun')),
                ('pipeline_run', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='artifacts', to='astrolift_pipelines.pipelinerun')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['pipeline_run', 'name'],
            },
        ),
        migrations.CreateModel(
            name='Trigger',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('version', models.IntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('deleted_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('guid', core.fields.uuid_v7.UUIDv7Field(db_index=True, default=core.fields.uuid_v7.uuid7, editable=False, unique=True)),
                ('kind', models.CharField(choices=[('push', 'Push'), ('pull_request', 'Pull Request'), ('schedule', 'Schedule'), ('manual', 'Manual'), ('webhook', 'Webhook')], max_length=32)),
                ('config', models.JSONField(blank=True, default=dict)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='Creator')),
                ('deleted_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('pipeline', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='triggers', to='astrolift_pipelines.pipeline')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['pipeline', 'kind'],
            },
        ),
        migrations.AddIndex(
            model_name='pipeline',
            index=models.Index(fields=['organization', 'name'], name='pipeline_org_name_idx'),
        ),
        migrations.AddIndex(
            model_name='pipelinerun',
            index=models.Index(fields=['pipeline', '-run_number'], name='prun_pipeline_run_number_idx'),
        ),
        migrations.AddIndex(
            model_name='pipelinerun',
            index=models.Index(fields=['status'], name='prun_status_idx'),
        ),
        migrations.AddIndex(
            model_name='jobrun',
            index=models.Index(fields=['pipeline_run', 'status'], name='jrun_prun_status_idx'),
        ),
    ]
