"""Astrolift forms app.

Form definitions + submissions. Backs the ``/forms/*`` UI shipped in
#437 scope B (preview tab, submissions tab, builder). The GraphQL
contract is read straight off ``frontend/graphql/forms/forms.queries.ts``
— this app is the gap-filling backend for the FE bundle that landed
without a server.
"""

default_app_config = "astrolift_forms.apps.AstroliftFormsConfig"
