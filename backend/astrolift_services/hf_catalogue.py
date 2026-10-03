"""Bounded, anonymous reads of the public Hugging Face model catalogue."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import strawberry
from django.core import signing
from django.utils import timezone

ORIGIN = "https://huggingface.co"
MAX_BODY_BYTES = 1_048_576
MAX_ITEMS = 30
TIMEOUT_SECONDS = 5
CURSOR_MAX_AGE = 1200
_SALT = "astrolift.hf-catalogue.v1"
_ATOM = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
_SHA = re.compile(r"^[a-fA-F0-9]{40}$")
_EXPAND = [
    "sha",
    "author",
    "pipeline_tag",
    "library_name",
    "cardData",
    "gated",
    "tags",
    "downloads",
    "likes",
    "config",
    "private",
    "disabled",
]


@strawberry.enum
class CatalogueState(Enum):
    AVAILABLE = "available"
    NO_DATA = "no_data"
    UNAVAILABLE = "unavailable"
    RATE_LIMITED = "rate_limited"


@strawberry.enum
class ModelGating(Enum):
    NONE = "none"
    AUTO = "auto"
    MANUAL = "manual"
    UNKNOWN = "unknown"


@strawberry.enum
class ModelCompatibility(Enum):
    UNKNOWN = "unknown"


@strawberry.type
class HuggingFaceModel:
    repo_id: str
    revision_sha: str | None
    author: str | None
    pipeline_tag: str | None
    library: str | None
    license: str | None
    gated: ModelGating
    architectures: list[str]
    downloads: float | None
    likes: float | None
    compatibility: ModelCompatibility = ModelCompatibility.UNKNOWN


@strawberry.type
class HuggingFaceModelsPage:
    state: CatalogueState
    observed_at: datetime
    items: list[HuggingFaceModel]
    next_cursor: str | None = None
    retry_after_seconds: int | None = None
    source: str = "huggingface_public_api"


@strawberry.type
class HuggingFaceModelResult:
    state: CatalogueState
    observed_at: datetime
    model: HuggingFaceModel | None = None
    source: str = "huggingface_public_api"
    retry_after_seconds: int | None = None


@dataclass(frozen=True)
class CatalogueFilters:
    search: str = ""
    author: str = ""
    pipeline_tag: str = ""
    library: str = ""
    license: str = ""
    gated: bool | None = None
    sort_by: str = "downloads"
    first: int = 20

    def params(self) -> list[tuple[str, str]]:
        if (
            not isinstance(self.first, int)
            or isinstance(self.first, bool)
            or not 1 <= self.first <= MAX_ITEMS
        ):
            raise ValueError("first must be between 1 and 30")
        if self.sort_by not in {"downloads", "likes", "lastModified", "createdAt", "trendingScore"}:
            raise ValueError("unsupported catalogue sort")
        if len(self.search) > 120 or any(ord(c) < 32 for c in self.search):
            raise ValueError("search must be at most 120 printable characters")
        for value in (self.author, self.pipeline_tag, self.library, self.license):
            if value and not _ATOM.fullmatch(value):
                raise ValueError("invalid catalogue filter")
        params = [("limit", str(self.first)), ("sort", self.sort_by)]
        for key, value in (
            ("search", self.search.strip()),
            ("author", self.author),
            ("pipeline_tag", self.pipeline_tag),
        ):
            if value:
                params.append((key, value))
        if self.library:
            params.append(("filter", self.library))
        if self.license:
            params.append(("filter", f"license:{self.license}"))
        if self.gated is not None:
            params.append(("gated", "true" if self.gated else "false"))
        params.extend(("expand", key) for key in _EXPAND)
        return params


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class CatalogueUnavailable(Exception):
    def __init__(self, state=CatalogueState.UNAVAILABLE, retry_after=None):
        self.state = state
        self.retry_after = retry_after


def _request(url: str):
    request = Request(
        url, headers={"Accept": "application/json", "User-Agent": "Astrolift-public-model-catalogue/1"}
    )
    try:
        with build_opener(_NoRedirect()).open(request, timeout=TIMEOUT_SECONDS) as response:
            body = response.read(MAX_BODY_BYTES + 1)
            link = response.headers.get("Link", "")
            if len(body) > MAX_BODY_BYTES or len(link) > 8192:
                raise CatalogueUnavailable()
            return json.loads(body), link
    except HTTPError as exc:
        retry = exc.headers.get("Retry-After", "") if exc.headers else ""
        seconds = int(retry) if retry.isdigit() and len(retry) <= 6 else None
        raise CatalogueUnavailable(
            CatalogueState.RATE_LIMITED
            if exc.code == 429
            else CatalogueState.NO_DATA
            if exc.code == 404
            else CatalogueState.UNAVAILABLE,
            min(seconds, 86400) if seconds is not None else None,
        ) from None
    except (URLError, TimeoutError, OSError, ValueError, RecursionError) as exc:
        raise CatalogueUnavailable() from exc


def _text(value, maximum=256):
    return (
        value
        if isinstance(value, str) and len(value) <= maximum and not any(ord(c) < 32 for c in value)
        else None
    )


def valid_repo_id(value: str) -> bool:
    parts = value.split("/")
    return len(parts) in (1, 2) and all(
        _ATOM.fullmatch(part) and ".." not in part and "--" not in part and not part.endswith(".git")
        for part in parts
    )


def _counter(value):
    return (
        float(value)
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 2**53
        else None
    )


def _model(row: object, *, allow_private: bool = False) -> HuggingFaceModel | None:
    if not isinstance(row, dict):
        raise CatalogueUnavailable()
    repo = row.get("id") or row.get("modelId")
    if not isinstance(repo, str) or not valid_repo_id(repo):
        raise CatalogueUnavailable()
    if (row.get("private") is True and not allow_private) or row.get("disabled") is True:
        return None
    card = row.get("cardData")
    card = card if isinstance(card, dict) else {}
    config = row.get("config")
    config = config if isinstance(config, dict) else {}
    architectures = config.get("architectures")
    architectures = architectures if isinstance(architectures, list) else []
    license = _text(card.get("license"))
    if license is None and isinstance(row.get("tags"), list):
        license = next(
            (_text(tag[8:]) for tag in row["tags"] if isinstance(tag, str) and tag.startswith("license:")),
            None,
        )
    gated = row.get("gated")
    gating = (
        ModelGating.NONE
        if gated is False
        else ModelGating.AUTO
        if gated == "auto"
        else ModelGating.MANUAL
        if gated == "manual"
        else ModelGating.UNKNOWN
    )
    sha = row.get("sha")
    return HuggingFaceModel(
        repo_id=repo,
        revision_sha=sha.lower() if isinstance(sha, str) and _SHA.fullmatch(sha) else None,
        author=_text(row.get("author")),
        pipeline_tag=_text(row.get("pipeline_tag")),
        library=_text(row.get("library_name")),
        license=license,
        gated=gating,
        architectures=[v for a in architectures[:16] if (v := _text(a)) is not None],
        downloads=_counter(row.get("downloads")),
        likes=_counter(row.get("likes")),
    )


def _page_url(url: str, params) -> bool:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "huggingface.co"
        or parsed.path != "/api/models"
        or parsed.fragment
    ):
        return False
    actual = parse_qs(parsed.query, keep_blank_values=True)
    expected = parse_qs(urlencode(params), keep_blank_values=True)
    cursor = actual.pop("cursor", None)
    return actual == expected and (cursor is None or len(cursor) == 1 and 0 < len(cursor[0]) <= 4096)


def search_models(filters: CatalogueFilters, *, after: str | None, audience: str) -> HuggingFaceModelsPage:
    params = filters.params()
    fingerprint = hashlib.sha256(urlencode(params).encode()).hexdigest()
    url = f"{ORIGIN}/api/models?{urlencode(params)}"
    if after:
        try:
            if len(after) > 12000:
                raise ValueError("invalid catalogue cursor")
            cursor = signing.loads(after, salt=_SALT, max_age=CURSOR_MAX_AGE)
            if (
                not isinstance(cursor, dict)
                or cursor.get("filters") != fingerprint
                or cursor.get("audience") != audience
                or not _page_url(cursor.get("url", ""), params)
            ):
                raise ValueError("invalid catalogue cursor")
            url = cursor["url"]
        except (signing.BadSignature, TypeError, ValueError, AttributeError) as exc:
            raise ValueError("invalid or expired catalogue cursor") from exc
    now = timezone.now()
    try:
        rows, link = _request(url)
        if not isinstance(rows, list) or len(rows) > filters.first:
            raise CatalogueUnavailable()
        items = [item for row in rows if (item := _model(row)) is not None]
        if len({item.repo_id for item in items}) != len(items):
            raise CatalogueUnavailable()
        links = re.findall(r'<([^<>]+)>\s*;\s*rel="next"', link)
        next_cursor = None
        if links:
            if len(links) != 1 or not _page_url(links[0], params) or links[0] == url:
                raise CatalogueUnavailable()
            next_cursor = signing.dumps(
                {"url": links[0], "filters": fingerprint, "audience": audience}, salt=_SALT, compress=True
            )
        return HuggingFaceModelsPage(
            state=CatalogueState.AVAILABLE if items else CatalogueState.NO_DATA,
            observed_at=now,
            items=items,
            next_cursor=next_cursor,
        )
    except CatalogueUnavailable as exc:
        return HuggingFaceModelsPage(
            state=exc.state, observed_at=now, items=[], retry_after_seconds=exc.retry_after
        )


def model_detail(repo_id: str, revision: str | None) -> HuggingFaceModelResult:
    if not valid_repo_id(repo_id):
        raise ValueError("invalid model repository ID")
    if revision is not None and (
        not 1 <= len(revision) <= 128 or not re.fullmatch(r"[A-Za-z0-9_.\-/]+", revision) or ".." in revision
    ):
        raise ValueError("invalid model revision")
    url = f"{ORIGIN}/api/models/{quote(repo_id, safe='/')}"
    if revision is not None:
        url += f"/revision/{quote(revision, safe='')}"
    url += "?" + urlencode([("expand", key) for key in _EXPAND])
    now = timezone.now()
    try:
        row, _link = _request(url)
        item = _model(row)
        if item is not None and (
            item.repo_id != repo_id
            or item.revision_sha is None
            or revision
            and _SHA.fullmatch(revision)
            and item.revision_sha != revision.lower()
        ):
            raise CatalogueUnavailable()
        return HuggingFaceModelResult(
            state=CatalogueState.AVAILABLE if item else CatalogueState.NO_DATA, observed_at=now, model=item
        )
    except CatalogueUnavailable as exc:
        return HuggingFaceModelResult(state=exc.state, observed_at=now, retry_after_seconds=exc.retry_after)
