from __future__ import annotations

from typing import Any

PAIRED_OPSCODE_REVISION = "3ebe6356740272773f6216b7f98c70cb08a93af0"

UUID = "123e4567-e89b-12d3-a456-426614174000"
TOKEN = "private-bearer-do-not-log"
PUBLIC_SPECS = {
    "MachineImage": {
        "architecture": "amd64",
        "buildDate": "2026-11-07",
        "nico": {"ipxeScript": "#!ipxe\nchain https://boot.example.com/image.ipxe\n"},
        "operatingSystem": {"family": "ubuntu", "version": "24.04"},
    },
    "ClusterRelease": {
        "machineImageRef": {"name": "image-a"},
        "machineInit": {"userDataTemplate": "#cloud-config\n{}\n"},
    },
    "NodePoolRelease": {
        "machineImageRef": {"name": "image-a"},
        "machineInit": {"userDataTemplate": "#cloud-config\n{}\n"},
    },
    "ClusterReleaseChannel": {"releases": {"1.36": [{"name": "release-a", "minimumUpgradeAge": "0s"}]}},
    "ControlPlaneClass": {
        "controlPlane": {"instanceTypePreferences": [UUID], "replicas": 3, "systemReservedMemory": "16Gi"},
        "rollout": {"strategy": {"type": "RollingUpdate", "rollingUpdate": {"maxSurge": 1}}},
    },
    "NVKECluster": {
        "apiServerAccess": {"publicAccessEnabled": False},
        "clusterNetwork": {
            "pods": {"cidrBlocks": ["192.168.0.0/16"]},
            "services": {"cidrBlocks": ["10.96.0.0/12"]},
            "serviceDomain": "cluster.local",
        },
        "controlPlaneClass": "standard",
        "release": {"channel": {"name": "stable"}},
    },
}


class Peer:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.status = 200
        self.body = b'{"items":[],"futureField":{"kept":true}}'
        self.headers: dict[str, str] = {"Content-Type": "application/json", "ETag": '"opaque-rv-17"'}
        self.disconnect = False
        self.url = ""
        self.verify: Any = True
