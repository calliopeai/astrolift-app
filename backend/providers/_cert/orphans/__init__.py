"""Post-teardown orphan scanners for the certification campaign (spec 43 §3.2).

One module per cloud, one shared report shape, and a single rule: a scan that
finds anything, or fails to read anything, raises.
"""

from _cert.orphans.model import (
    CloudResource,
    Orphan,
    OrphansFound,
    ScanError,
    ScanReport,
    scan_families,
)

__all__ = [
    "CloudResource",
    "Orphan",
    "OrphansFound",
    "ScanError",
    "ScanReport",
    "scan_families",
]
