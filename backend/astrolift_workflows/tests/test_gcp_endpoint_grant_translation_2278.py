from types import SimpleNamespace

from astrolift_workflows.activities.workload_identity import _permissions_from_bindings


def test_endpoint_roles_retain_each_resource_and_deduplicate_only_exact_pairs():
    first = "projects/fixture-project/locations/us-central1/endpoints/1"
    second = "projects/fixture-project/locations/us-central1/endpoints/2"
    role = "roles/aiplatform.user"
    binding = SimpleNamespace(
        iam_grants=[SimpleNamespace(resource=resource, actions=[role]) for resource in (first, second, first)]
    )
    assert _permissions_from_bindings([binding], plugin_slug="gcp") == [
        {"role": role, "resource": first},
        {"role": role, "resource": second},
    ]
