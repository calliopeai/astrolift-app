"""bootstrap_admin has to be able to create an Organization on a fresh database.

It could not. ``Organization.website`` is ``URLField(blank=True, default="")``,
which is NOT NULL in Postgres -- ``blank`` is a form-layer concept and says
nothing about the column -- while the command passed ``org_website or None``,
so an install that named no website sent a null and the insert was refused:

    psycopg2.errors.NotNullViolation: null value in column "website" of
    relation "astrolift_identity_organization" violates not-null constraint

Nothing downstream survives that. ``bootstrap_idp`` runs next and binds the IdP
to the org this command creates, so it failed with "organization with slug=...
does not exist", no IdP row was written, and ``active_idp.json`` answered
``{"kind": "local"}`` -- a local-only login page on an install whose AUTH0_*
and ASTROLIFT_IDP_* variables were all set correctly. Seen live on the CONFLICT
install (calliope-installer #306 got the email through; this is the next link).
"""

from __future__ import annotations

import pytest
from django.core.management import call_command

from astrolift_identity.models.organization import Organization


@pytest.mark.django_db
def test_an_org_is_created_when_no_website_is_given():
    """The path every install takes: nobody passes --org-website."""
    call_command(
        "bootstrap_admin",
        org="acme",
        org_name="Acme Corp",
        email="admin@acme.example",
    )

    org = Organization.objects.get(slug="acme")
    # Blank, and blank is the empty string this column actually allows.
    assert org.website == ""
    assert org.name == "Acme Corp"


@pytest.mark.django_db
def test_a_website_is_kept_when_one_is_given():
    """The other half, so the fix cannot become "always blank"."""
    call_command(
        "bootstrap_admin",
        org="acme",
        org_name="Acme Corp",
        email="admin@acme.example",
        org_website="https://acme.example",
    )

    assert Organization.objects.get(slug="acme").website == "https://acme.example"
