"""Bounded response readers for untrusted SCM source archives."""

from __future__ import annotations

MAX_ARCHIVE_DOWNLOAD_BYTES = 256 * 1024 * 1024


class ArchiveDownloadTooLarge(ValueError):
    """The remote archive exceeds the platform's compressed-size limit."""


def _check_content_length(response, limit: int) -> None:
    headers = getattr(response, "headers", None)
    if not headers:
        return
    raw = headers.get("Content-Length") or headers.get("content-length")
    try:
        length = int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        return
    if length > limit:
        raise ArchiveDownloadTooLarge(f"source archive exceeds {limit} bytes")


def read_urllib_response(response, *, limit: int = MAX_ARCHIVE_DOWNLOAD_BYTES) -> bytes:
    """Read at most ``limit + 1`` bytes from an urllib response."""
    _check_content_length(response, limit)
    data = response.read(limit + 1)
    if len(data) > limit:
        raise ArchiveDownloadTooLarge(f"source archive exceeds {limit} bytes")
    return data


def read_requests_response(response, *, limit: int = MAX_ARCHIVE_DOWNLOAD_BYTES) -> bytes:
    """Read a streamed requests response without crossing ``limit`` bytes."""
    _check_content_length(response, limit)
    iterator = getattr(response, "iter_content", None)
    if not callable(iterator):
        data = response.content
        if len(data) > limit:
            raise ArchiveDownloadTooLarge(f"source archive exceeds {limit} bytes")
        return data

    chunks: list[bytes] = []
    total = 0
    for chunk in iterator(chunk_size=1024 * 1024):
        if not chunk:
            continue
        total += len(chunk)
        if total > limit:
            raise ArchiveDownloadTooLarge(f"source archive exceeds {limit} bytes")
        chunks.append(chunk)
    return b"".join(chunks)
