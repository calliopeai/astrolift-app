# Merge migration: reconcile the two 0012_* leaves created by parallel branches.
#
# 0012_astroliftsession.py — session tracking model (#480/#495/#498)
# 0012_deviceflowsession.py — device-flow auth (#475)
# 0013_deviceflowsession_enrollment.py — QR install enrollment (#494),
#   already chains off 0012_deviceflowsession
#
# This merge node joins the AstroliftSession branch into the chain so
# Django can resolve a single leaf.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("astrolift_identity", "0012_astroliftsession"),
        ("astrolift_identity", "0013_deviceflowsession_enrollment"),
    ]

    operations = []
