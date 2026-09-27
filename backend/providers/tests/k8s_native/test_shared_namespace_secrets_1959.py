"""Tenant Secret references are refused in a namespace shared between tenants (#1959)."""

from __future__ import annotations

import copy

import pytest

from k8s_native.managed._secret_refs import secret_names_in
from k8s_native.managed.faas_knative import KnativeServiceConfig, KnativeServiceDriver
from k8s_native.managed.model_endpoint_kserve import KServeConfig, KServeDriver
from k8s_native.managed.mssql_express import SQLServerExpressConfig, SQLServerExpressDriver
from k8s_native.managed.opensearch_operator import OpenSearchOperatorConfig, OpenSearchSearchDriver
from k8s_native.managed.workflow_argo import ArgoWorkflowsConfig, ArgoWorkflowsDriver

from .test_faas_knative import IMAGE as KNATIVE_IMAGE
from .test_model_endpoint_kserve import _inference_spec
from .test_workflow_argo import _workflow_spec


def test_every_pod_spec_secret_reference_is_found():
    spec = {
        "containers": [
            {
                "env": [{"name": "A", "valueFrom": {"secretKeyRef": {"name": "one", "key": "k"}}}],
                "envFrom": [{"secretRef": {"name": "two"}}],
            }
        ],
        "volumes": [{"name": "v", "secret": {"secretName": "three"}}],
        "imagePullSecrets": [{"name": "four"}],
        "image_pull_secrets": ["five"],
    }
    assert secret_names_in(spec) == {"one", "two", "three", "four", "five"}


def _knative(namespace):
    return KnativeServiceDriver(config=KnativeServiceConfig(namespace=namespace))


def _kserve_cfg():
    spec = _inference_spec()
    spec["predictor"]["model"]["env"] = [
        {"name": "TOKEN", "valueFrom": {"secretKeyRef": {"name": "other", "key": "t"}}}
    ]
    return {"inference_spec": spec}


def _argo_cfg():
    spec = _workflow_spec()
    spec["templates"][0]["container"]["envFrom"] = [{"secretRef": {"name": "other"}}]
    return {"workflow_spec": spec}


@pytest.mark.parametrize(
    "validate",
    [
        lambda ns: _knative(ns)._normalized_config(
            "small",
            {"image": KNATIVE_IMAGE, "secret_env": {"TOKEN": {"secret_name": "other", "key": "t"}}},
            update=False,
        ),
        lambda ns: _knative(ns)._normalized_config(
            "small", {"image": KNATIVE_IMAGE, "image_pull_secrets": ["other"]}, update=False
        ),
        lambda ns: KServeDriver(config=KServeConfig(namespace=ns))._normalize(copy.deepcopy(_kserve_cfg())),
        lambda ns: ArgoWorkflowsDriver(config=ArgoWorkflowsConfig(namespace=ns))._normalize(copy.deepcopy(_argo_cfg())),
        lambda ns: SQLServerExpressDriver(config=SQLServerExpressConfig(namespace=ns))._service_config(
            "small", {"tls_secret_name": "other"}
        ),
        # A pull secret is a registry credential too; these two drivers left it out (#2087).
        lambda ns: SQLServerExpressDriver(config=SQLServerExpressConfig(namespace=ns))._service_config(
            "small", {"image_pull_secrets": ["other"]}
        ),
        lambda ns: OpenSearchSearchDriver(config=OpenSearchOperatorConfig(namespace=ns))._service_config(
            "small", {"image_pull_secrets": ["other"], "index_prefix": "app-"}, spec=None
        ),
    ],
    ids=[
        "knative-secret-env",
        "knative-pull-secret",
        "kserve",
        "argo",
        "mssql-tls",
        "mssql-pull-secret",
        "opensearch-pull-secret",
    ],
)
def test_secret_refs_are_refused_only_in_a_shared_namespace(validate):
    validate(None)  # the default per-app namespace holds only this tenant's Secrets
    with pytest.raises(ValueError, match="shared between tenants"):
        validate("shared-workloads")
