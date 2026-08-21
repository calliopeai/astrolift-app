"""Drop AgentBox.session_name (#1470).

Declared with a default, projected onto the GraphQL type, and written by
nothing: no mutation input reaches it, so it only ever held the model
default. The three places that read it had to agree with each other and
with the in-pod keep-alive loop's ``has-session`` target, and a per-box
value is what would let them disagree. They read the constant now, so the
column has nothing left to hold.

``sessionName`` stays on the GraphQL type, projecting the constant, so the
CLI's contract does not change.
"""

from __future__ import annotations

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("astrolift_agents", "0022_agentbox"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="agentbox",
            name="session_name",
        ),
    ]
