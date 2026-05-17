"""GraphQL types for the forms surface (#453).

The shape is dictated by the shipped #437 frontend in
``frontend/graphql/forms/forms.queries.ts``. ``status`` stays as a
plain lowercase string on the wire (``draft`` / ``published`` /
``archived``) because the FE reads those literals directly — using a
Strawberry enum here would force uppercase member names on the wire
and break the existing components without buying any safety
([[strawberry_enum_wire_format]]).

The GraphQL ``version`` field is sourced from the model column
``schema_version`` so we don't collide with the row-level tracking
``version`` carried by :class:`TrackingMixin`.
"""

from __future__ import annotations

import datetime as dt

import strawberry

from astrolift_graphql import GUID


@strawberry.type(name="AstroliftFormDefinition")
class FormDefinitionType:
    id: GUID
    slug: str
    name: str
    description: str
    schema: strawberry.scalars.JSON
    field_config: strawberry.scalars.JSON
    logic_rules: strawberry.scalars.JSON
    scoring: strawberry.scalars.JSON
    status: str
    form_type: str
    is_public: bool
    version: int
    """The form's schema version. Bumped on every publish so submissions
    can be tied to the exact schema the submitter saw."""

    submission_count: int
    published_at: dt.datetime | None
    created_at: dt.datetime
    updated_at: dt.datetime
    created_by_username: str | None
    updated_by_username: str | None


@strawberry.type(name="AstroliftFormSubmission")
class FormSubmissionType:
    id: GUID
    form_slug: str
    form_name: str
    form_version: int
    submitted_at: dt.datetime
    created_at: dt.datetime
    submitter_display_name: str
    submitter_email: str
    payload: strawberry.scalars.JSON
    source_ip: str | None
    user_agent: str
    status: str
