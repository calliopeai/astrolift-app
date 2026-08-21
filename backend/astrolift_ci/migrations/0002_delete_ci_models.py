"""Drop the CiPipeline / CiRun / CiJob / CiStep tables (#1528).

Four models created by `0001_initial`, in `INSTALLED_APPS`, registered in
the admin — and written by nothing outside their own tests. No GraphQL
type, no query, no mutation, no workflow, no management command, and
`services/ci_yaml_parser.py` had no importers either.

`astrolift_ci_convert` is unrelated despite the name: it uses its own
`common/types.py`, not these models. The live pipeline system is
`astrolift_pipelines`, and this was a parallel earlier one that was never
connected to anything.

The cost was not hypothetical: tracing "pipeline runs capture no logs
anywhere" (#1218) meant ruling out `CiStep.log_output` as one of three
parallel log stores before finding the real gap.

The app package stays until this migration has been applied everywhere;
removing it in the same change would strand the tables in any database
that had not yet run it. The empty shell goes in a follow-up.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('astrolift_ci', '0001_initial'),
    ]

    operations = [
        migrations.RemoveIndex(
            model_name='cijob',
            name='cijob_run_status_idx',
        ),
        migrations.RemoveIndex(
            model_name='cipipeline',
            name='cipipeline_org_status_idx',
        ),
        migrations.RemoveConstraint(
            model_name='cipipeline',
            name='cipipeline_slug_unique_active_per_org',
        ),
        migrations.RemoveIndex(
            model_name='cirun',
            name='cirun_pipeline_status_idx',
        ),
        migrations.RemoveIndex(
            model_name='cistep',
            name='cistep_job_order_idx',
        ),
        migrations.RemoveField(
            model_name='cijob',
            name='created_by',
        ),
        migrations.RemoveField(
            model_name='cijob',
            name='deleted_by',
        ),
        migrations.RemoveField(
            model_name='cijob',
            name='run',
        ),
        migrations.RemoveField(
            model_name='cijob',
            name='updated_by',
        ),
        migrations.RemoveField(
            model_name='cirun',
            name='created_by',
        ),
        migrations.RemoveField(
            model_name='cirun',
            name='deleted_by',
        ),
        migrations.RemoveField(
            model_name='cirun',
            name='pipeline',
        ),
        migrations.RemoveField(
            model_name='cirun',
            name='updated_by',
        ),
        migrations.RemoveField(
            model_name='cistep',
            name='created_by',
        ),
        migrations.RemoveField(
            model_name='cistep',
            name='deleted_by',
        ),
        migrations.RemoveField(
            model_name='cistep',
            name='job',
        ),
        migrations.RemoveField(
            model_name='cistep',
            name='updated_by',
        ),
        migrations.DeleteModel(
            name='CiPipeline',
        ),
        migrations.DeleteModel(
            name='CiRun',
        ),
        migrations.DeleteModel(
            name='CiJob',
        ),
        migrations.DeleteModel(
            name='CiStep',
        ),
    ]
