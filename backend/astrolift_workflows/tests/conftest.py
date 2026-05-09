"""Shared fixtures for workflow + activity tests.

Re-exports the lifecycle test scaffolding so workflow tests can lean
on the same Org / Project / App / Env / Cluster shapes that lifecycle
tests use without copy-pasting. pytest auto-collects fixtures from
sibling conftests, so importing them here makes them visible to this
package's tests.
"""

from astrolift_lifecycle.tests.conftest import (  # noqa: F401
    actor,
    app,
    cluster,
    env,
    env_requires_approval,
    fake_info,
    fake_info_other,
    no_temporal,
    org,
    other_actor,
    project,
    provider_plugin,
    team,
    temporal_recorder,
    _no_opensearch_profile_index,
)
