"""ECR scan-findings read-back (#313).

``ensure_repo`` has always created repos with ``scanOnPush`` on, so ECR
scanned every pushed image and no code ever called
``DescribeImageScanFindings``. The deploy gate needs that read, and it
needs "not scanned" to be distinguishable from "clean".
"""

from __future__ import annotations

import datetime as dt

import pytest

from aws._errors import NotFoundError
from aws.registry_ecr import ECRConfig, ECRDriver


class _Exceptions:
    class ScanNotFoundException(Exception):
        pass

    class ImageNotFoundException(Exception):
        pass

    class RepositoryNotFoundException(Exception):
        pass

    class RepositoryAlreadyExistsException(Exception):
        pass


class _FakeEcr:
    """Records the calls and replays canned describe_image_scan_findings
    pages. moto's ECR scan surface doesn't model paging, which is the part
    that matters here."""

    exceptions = _Exceptions

    def __init__(self, pages=None, raises: Exception | None = None) -> None:
        self.pages = pages or []
        self.raises = raises
        self.calls: list[dict] = []

    def describe_image_scan_findings(self, **kwargs):
        self.calls.append(kwargs)
        if self.raises is not None:
            raise self.raises
        return self.pages[len(self.calls) - 1]


def _driver(client) -> ECRDriver:
    return ECRDriver(
        config=ECRConfig(region="us-east-1", account_id="111122223333"),
        client=client,
    )


def _finding(name: str, severity: str, *, package="openssl", version="1.1.1"):
    return {
        "name": name,
        "severity": severity,
        "description": f"{name} detail",
        "attributes": [
            {"key": "package_name", "value": package},
            {"key": "package_version", "value": version},
        ],
    }


def test_complete_scan_maps_counts_and_findings() -> None:
    client = _FakeEcr(
        pages=[
            {
                "imageScanStatus": {"status": "COMPLETE", "description": "done"},
                "imageScanFindings": {
                    "imageScanCompletedAt": dt.datetime(2026, 8, 21, 12, 0, 0, tzinfo=dt.UTC),
                    "findingSeverityCounts": {"CRITICAL": 1, "HIGH": 1, "INFORMATIONAL": 4},
                    "findings": [
                        _finding("CVE-2026-1", "CRITICAL"),
                        _finding("CVE-2026-2", "HIGH", package="zlib"),
                        _finding("CVE-2026-3", "INFORMATIONAL"),
                    ],
                },
            }
        ]
    )

    result = _driver(client).get_scan_findings(repo="acme/api", digest="sha256:" + "a" * 64)

    assert result["status"] == "COMPLETE"
    assert result["severity_counts"] == {"critical": 1, "high": 1, "medium": 0, "low": 0}
    # INFORMATIONAL is not a policy severity and is dropped, not folded into low.
    assert [f["cve_id"] for f in result["findings"]] == ["CVE-2026-1", "CVE-2026-2"]
    assert result["findings"][1]["package_name"] == "zlib"
    assert result["completed_at"].startswith("2026-08-21")
    assert client.calls[0]["imageId"] == {"imageDigest": "sha256:" + "a" * 64}


def test_findings_are_paged_to_exhaustion() -> None:
    """A truncated finding list under-counts a policy that walks findings,
    so every page has to be drained."""
    client = _FakeEcr(
        pages=[
            {
                "imageScanStatus": {"status": "COMPLETE"},
                "imageScanFindings": {
                    "findingSeverityCounts": {"CRITICAL": 2},
                    "findings": [_finding("CVE-2026-1", "CRITICAL")],
                },
                "nextToken": "page-2",
            },
            {
                "imageScanStatus": {"status": "COMPLETE"},
                "imageScanFindings": {
                    # Counts repeat on every page; taking them twice would
                    # not double them, but the findings must accumulate.
                    "findingSeverityCounts": {"CRITICAL": 2},
                    "findings": [_finding("CVE-2026-2", "CRITICAL")],
                },
            },
        ]
    )

    result = _driver(client).get_scan_findings(repo="acme/api", digest="sha256:b")

    assert [f["cve_id"] for f in result["findings"]] == ["CVE-2026-1", "CVE-2026-2"]
    assert result["severity_counts"]["critical"] == 2
    assert client.calls[1]["nextToken"] == "page-2"


def test_never_scanned_image_reports_not_found_not_clean() -> None:
    client = _FakeEcr(raises=_Exceptions.ScanNotFoundException("no scan for image"))

    result = _driver(client).get_scan_findings(repo="acme/api", digest="sha256:c")

    assert result["status"] == "NOT_FOUND"
    assert result["findings"] == []


def test_missing_image_raises_not_found() -> None:
    client = _FakeEcr(raises=_Exceptions.ImageNotFoundException("gone"))

    with pytest.raises(NotFoundError):
        _driver(client).get_scan_findings(repo="acme/api", digest="sha256:d")
