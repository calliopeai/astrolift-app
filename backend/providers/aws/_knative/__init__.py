"""Vendored Knative Operator install manifests (chart-free).

The Knative Operator is published upstream only as an OCI Helm chart in the
``knative-releases`` Artifact Registry, and that artifact returns ``403`` to
unauthenticated pulls — so Flux can't install it and the old chart-based
bootstrap never worked. Instead we vendor the official *chart-free* operator
YAML (the ``kubectl apply -f`` install) for a pinned Knative release and apply
it directly as a bootstrap component's ``post_install_manifests``.

``KNATIVE_OPERATOR_MANIFESTS`` is the parsed manifest list — the
``knative-operator`` Namespace, the ``operator.knative.dev`` CRDs
(``KnativeServing`` / ``KnativeEventing``), and the operator's Deployments,
RBAC, webhooks, and ConfigMaps. The install path applies these ahead of the
``KnativeServing`` CR because it sorts foundational kinds (Namespace /
CustomResourceDefinition) first, so the CR's CRD is registered before the CR
lands.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

# The vendored install YAML lives next to this module so the path resolves
# relative to the package regardless of the working directory or where the
# package is bind-mounted at runtime.
_OPERATOR_YAML_PATH = Path(__file__).parent / "knative-operator-v1.16.0.yaml"


def load_operator_manifests() -> list[dict[str, Any]]:
    """Parse the vendored Knative Operator YAML into a list of manifest dicts.

    Empty / ``null`` documents (upstream multi-doc YAML often ends on a
    trailing ``---`` that parses to ``None``) are dropped so callers get only
    applyable objects.
    """
    with _OPERATOR_YAML_PATH.open(encoding="utf-8") as fh:
        return [doc for doc in yaml.safe_load_all(fh) if doc]


# Parsed once at import — the file is static vendored content, so there's no
# reason to re-read it on every ``bootstrap_components`` call.
KNATIVE_OPERATOR_MANIFESTS: list[dict[str, Any]] = load_operator_manifests()
