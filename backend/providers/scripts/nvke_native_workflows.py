"""Review-bound native operator inputs; fail when the pinned Go declarations change."""

import re
from pathlib import Path
from typing import Any


def inventory(checkout: Path) -> list[dict[str, Any]]:
    base = "controller/internal/temporal/workflows/"
    declarations = [
        (
            "RenewK8sCertsV2",
            "RenewK8sCertsInput",
            "renew_k8s_certs.go",
            {"clusterID": "uuid.UUID", "thresholdDays": "int", "force": "bool", "scheduledFor": "*time.Time"},
            ["clusterID", "thresholdDays"],
        ),
        (
            "RotateEtcdEncryptionKey",
            "RotateEtcdEncryptionKeyInput",
            "rotate_etcd_encryption_key.go",
            {"clusterID": "uuid.UUID"},
            ["clusterID"],
        ),
        (
            "RetireLostEtcdRotationNode",
            "RetireLostEtcdRotationNodeInput",
            "retire_lost_etcd_rotation_node.go",
            {"clusterID": "uuid.UUID", "rotationWorkflowID": "string", "nodeID": "uuid.UUID"},
            ["clusterID", "rotationWorkflowID", "nodeID"],
        ),
    ]
    schemas = {
        "uuid.UUID": {"type": "string", "format": "uuid"},
        "int": {"type": "integer", "minimum": 1, "maximum": 9223372036854775807},
        "bool": {"type": "boolean", "default": False},
        "*time.Time": {"type": ["string", "null"], "format": "date-time"},
        "string": {"type": "string", "minLength": 1, "maxLength": 1000},
    }
    result = []
    registrations = (checkout / "controller/internal/temporal/register.go").read_text()
    for name, input_type, filename, expected, required in declarations:
        source = (checkout / (base + filename)).read_text()
        struct = re.search(rf"type {input_type} struct \{{(.*?)\n\}}", source, re.S)
        fields = (
            {tag: kind for kind, tag in re.findall(r'\w+\s+(\*?[\w.]+)\s+`json:"([^",]+)(?:,[^"]*)?"`', struct[1])}
            if struct
            else {}
        )
        if fields != expected or not re.search(rf"func {name}\(ctx workflow.Context, input {input_type}\)", source):
            raise ValueError("native NVKE workflow declarations changed; review the operator contract")
        if not re.search(rf"w\.RegisterWorkflow\(workflows\.{name}\)", registrations):
            raise ValueError("native NVKE workflow registration changed; review the operator contract")
        schema = {
            "type": "object",
            "additionalProperties": False,
            "required": required,
            "properties": {field: schemas[kind].copy() for field, kind in fields.items()},
        }
        if name == "RenewK8sCertsV2":
            schema["not"] = {
                "required": ["force", "scheduledFor"],
                "properties": {"force": {"enum": [True]}, "scheduledFor": {"type": "string"}},
            }
        result.append(
            {
                "name": name,
                "input_type": input_type,
                "source": base + filename,
                "permission": "nvke.admin",
                "input_schema": schema,
            }
        )
    return result
