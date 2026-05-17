# Merge migration for #519. Two duplicate no-op merges landed in
# astrolift_registry (``0015_merge_20260517_2229`` +
# ``0015_merge_duplicate_0014_merges``) as parallel agents both
# tried to collapse the 0014 leaf storm. Both files have the same
# dependencies + empty operations -- the result is yet another
# two-leaf storm one level up. This merge collapses both into a
# single 0016 leaf so the migration graph is unambiguous again.
#
# No-op operations -- purely a graph fix.

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_registry", "0015_merge_20260517_2229"),
        ("astrolift_registry", "0015_merge_duplicate_0014_merges"),
    ]

    operations = []
