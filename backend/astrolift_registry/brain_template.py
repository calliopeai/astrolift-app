"""The skeleton a new semantic layer starts from (#2424).

A Mimwell edition is a published file set with its own manifest,
``edition.json``, that lists every file with its sha256. Creating a brain
downloads the edition archive at a ref from the install's template source
(``BRAIN_TEMPLATE_REPO`` / ``BRAIN_TEMPLATE_ARCHIVE_URL``), keeps exactly the
files the manifest lists, and refuses the whole archive when one of them is
missing, differs from its hash, is a link or escapes the tree. Files the
manifest does not list (the edition repository's own docs site, say) are not
part of the edition and are left out.

Astrolift never runs template code. It writes two files of its own:

* ``client.config.json``: the brain's name and short name, the client slug
  and the substrate selection (``deploy.*`` and the legacy placement keys),
  each set as a JSON key;
* ``astrolift.toml``: ``gen-astrolift.py``'s mapping of that selection, plus
  ``[app] kind = "mimwell"`` (``brain_substrate.render_manifest``).
"""

from __future__ import annotations

import dataclasses
import hashlib
import io
import json
import re
import zipfile

from astrolift_scm.providers.repo_writes import TreeFile

DOCKERFILE = "extensions/portal-container/Dockerfile"
CONFIG = "client.config.json"
MANIFEST = "astrolift.toml"
EDITION = "edition.json"
#: An edition is a few megabytes; refuse anything far larger before unpacking.
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")


class TemplateError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclasses.dataclass(frozen=True)
class Edition:
    ref: str
    version: str
    files: tuple[TreeFile, ...]


def template_repo() -> str:
    from django.conf import settings

    return (getattr(settings, "BRAIN_TEMPLATE_REPO", "") or "ConflictHQ/mimwell").strip()


def default_ref() -> str:
    from django.conf import settings

    return (getattr(settings, "BRAIN_TEMPLATE_REF", "") or "main").strip()


def archive_url(ref: str) -> str:
    import urllib.parse

    from django.conf import settings

    pattern = (
        getattr(settings, "BRAIN_TEMPLATE_ARCHIVE_URL", "") or "https://codeload.github.com/{repo}/zip/{ref}"
    )
    return pattern.format(repo=template_repo(), ref=urllib.parse.quote(ref, safe="/"))


def validate_ref(ref: str) -> str:
    ref = (ref or "").strip()
    if not _REF.match(ref) or ".." in ref:
        raise TemplateError("VALIDATION", "templateRef must be a tag, branch or commit")
    return ref


def check_ref(ref: str) -> None:
    """Refuse a ref the template source does not serve, without downloading it."""
    import urllib.error
    import urllib.request

    request = urllib.request.Request(archive_url(ref), method="HEAD", headers={"User-Agent": "astrolift"})
    try:
        with urllib.request.urlopen(request, timeout=10):
            return
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise TemplateError("VALIDATION", f"the template {template_repo()} has no ref {ref!r}") from exc
        raise TemplateError("PRECONDITION", f"the template source answered {exc.code} for {ref!r}") from exc
    except urllib.error.URLError as exc:
        raise TemplateError("PRECONDITION", f"couldn't reach the template source: {exc.reason}") from exc


def download(ref: str) -> bytes:
    import urllib.error
    import urllib.request

    from astrolift_scm.providers.archive_download import ArchiveDownloadTooLarge, read_urllib_response

    request = urllib.request.Request(archive_url(ref), headers={"User-Agent": "astrolift"})
    try:
        with urllib.request.urlopen(request, timeout=60) as resp:
            return read_urllib_response(resp, limit=MAX_ARCHIVE_BYTES)
    except ArchiveDownloadTooLarge as exc:
        raise TemplateError("PRECONDITION", str(exc)) from exc
    except urllib.error.HTTPError as exc:
        raise TemplateError("PRECONDITION", f"the template source answered {exc.code} for {ref!r}") from exc
    except urllib.error.URLError as exc:
        raise TemplateError("PRECONDITION", f"couldn't reach the template source: {exc.reason}") from exc


def _safe(path: str) -> bool:
    parts = path.split("/")
    return (
        bool(path)
        and not path.startswith("/")
        and "\\" not in path
        and all(p not in ("", ".", "..") for p in parts)
    )


def read_edition(archive: bytes, *, ref: str) -> Edition:
    """The verified edition inside a source archive (one top-level directory)."""
    try:
        bundle = zipfile.ZipFile(io.BytesIO(archive))
    except zipfile.BadZipFile as exc:
        raise TemplateError("PRECONDITION", "the template archive is not a zip file") from exc
    entries: dict[str, zipfile.ZipInfo] = {}
    for info in bundle.infolist():
        if info.is_dir():
            continue
        _, _, rel = info.filename.partition("/")
        if not rel:
            continue
        entries[rel] = info
    if EDITION not in entries:
        raise TemplateError(
            "PRECONDITION", f"the template at {ref!r} has no {EDITION}; it is not a Mimwell edition"
        )
    try:
        manifest = json.loads(bundle.read(entries[EDITION]).decode("utf-8"))
        listed = manifest["files"]
    except (ValueError, KeyError, TypeError) as exc:
        raise TemplateError("PRECONDITION", f"the template's {EDITION} is not readable") from exc
    files: list[TreeFile] = []
    problems: list[str] = []
    for row in listed:
        path = str((row or {}).get("path") or "")
        info = entries.get(path)
        if not _safe(path) or info is None:
            problems.append(f"{path or '?'} (missing)")
            continue
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            problems.append(f"{path} (a link)")
            continue
        content = bundle.read(info)
        if hashlib.sha256(content).hexdigest() != str(row.get("sha256") or ""):
            problems.append(f"{path} (hash differs)")
            continue
        files.append(TreeFile(path=path, content=content, executable=bool(int(row.get("mode") or 0) & 0o111)))
    if problems:
        shown = ", ".join(problems[:5]) + (f" and {len(problems) - 5} more" if len(problems) > 5 else "")
        raise TemplateError("PRECONDITION", f"the template at {ref!r} does not match its {EDITION}: {shown}")
    paths = {f.path for f in files}
    if CONFIG not in paths:
        raise TemplateError("PRECONDITION", f"the template at {ref!r} has no {CONFIG}")
    files.append(TreeFile(path=EDITION, content=bundle.read(entries[EDITION])))
    return Edition(ref=ref, version=str(manifest.get("version") or ""), files=tuple(files))


def short_name(name: str) -> str:
    letters = re.sub(r"[^A-Za-z0-9]+", " ", name).strip().upper()
    return (letters.split()[0] if letters else "BRAIN")[:16]


def client_config(raw: bytes, *, slug: str, name: str, selection) -> bytes:
    """The edition's ``client.config.json`` with this brain's identity and
    substrate selection set (``brain_substrate.apply_to_config``). Only those
    keys change; everything else is the edition's."""
    from astrolift_registry.brain_substrate import apply_to_config

    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise TemplateError("PRECONDITION", f"the template's {CONFIG} is not JSON") from exc
    if not isinstance(data, dict):
        raise TemplateError("PRECONDITION", f"the template's {CONFIG} is not a JSON object")
    data = apply_to_config(data, selection, slug=slug, name=name)
    data["brain"] = {**data["brain"], "shortName": short_name(name)}
    return dump_config(data)


def dump_config(data: dict) -> bytes:
    return (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def scaffold(
    edition: Edition, *, slug: str, name: str, selection, environment_name: str = "production"
) -> list[TreeFile]:
    """Every file of the first commit: the edition, plus the two files Astrolift writes."""
    from astrolift_registry.brain_substrate import render_manifest

    if not selection.image and DOCKERFILE not in {f.path for f in edition.files}:
        raise TemplateError(
            "PRECONDITION",
            f"the template at {edition.ref!r} has no {DOCKERFILE}; give an image, or use an edition that ships it",
        )
    out: list[TreeFile] = []
    for item in edition.files:
        if item.path == MANIFEST:
            continue
        if item.path == CONFIG:
            item = TreeFile(
                path=CONFIG, content=client_config(item.content, slug=slug, name=name, selection=selection)
            )
        out.append(item)
    manifest = render_manifest(selection, slug=slug, display_name=name, environment_name=environment_name)
    out.append(TreeFile(path=MANIFEST, content=manifest.encode("utf-8")))
    return out
