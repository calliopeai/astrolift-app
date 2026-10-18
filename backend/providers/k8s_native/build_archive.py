"""Private immutable build-context transport for the real Kaniko driver."""

from dataclasses import asdict
from pathlib import Path

from _sdk.build import BuildContextArchive


def render_archive_secret(archive: BuildContextArchive, *, name: str, namespace: str):
    import json

    from k8s_native import build_archive_unpack

    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {"astrolift.io/managed-by": "platform", "astrolift.io/component": "build"},
        },
        "type": "Opaque",
        "stringData": {
            "config": json.dumps(asdict(archive)),
            "install.py": Path(build_archive_unpack.__file__).read_text(),
        },
    }


def attach_archive_context(job, *, secret_name: str):
    pod = job["spec"]["template"]["spec"]
    pod["automountServiceAccountToken"] = False
    job["spec"]["activeDeadlineSeconds"] = 1800
    pod["volumes"] = [
        {"name": "context", "emptyDir": {"sizeLimit": "2Gi"}},
        {"name": "archive", "secret": {"secretName": secret_name}},
    ]
    pod["initContainers"] = [
        {
            "name": "fetch-build-context",
            "image": "python:3.12-slim",
            "command": ["python", "/archive/install.py"],
            "resources": {"requests": {"cpu": "100m", "memory": "128Mi"}, "limits": {"memory": "512Mi"}},
            "volumeMounts": [
                {"name": "context", "mountPath": "/workspace"},
                {"name": "archive", "mountPath": "/archive", "readOnly": True},
            ],
        }
    ]
    pod["containers"][0]["volumeMounts"] = [{"name": "context", "mountPath": "/workspace"}]
