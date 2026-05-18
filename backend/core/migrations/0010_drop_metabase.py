# Drop the Metabase scaffolding inherited from the boilerworks
# template. Astrolift is a deployment platform; Metabase is a BI
# tool we never integrated against. The models, GraphQL types,
# REST client, and admin registrations all came in via the
# scaffold and were never wired to actual Metabase endpoints
# (METABASE_SITE_URL was always empty), so the tables are unused.
#
# Pure DeleteModel — no data preservation. If anyone has been
# silently writing to these tables, they'll get an error at
# write time instead of carrying the rows forward.

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0009_dataprocess_organization"),
    ]

    operations = [
        migrations.DeleteModel(name="MetabaseChart"),
        migrations.DeleteModel(name="MetabaseUnimportedChart"),
    ]
