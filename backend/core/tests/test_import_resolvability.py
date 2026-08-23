"""Every first-party import in the guarded trees resolves (#1590, #1614).

Four mutations imported names from `core.schema` that the package root does
not export, and one imported from `core.schema.process`, which is not a
module at all. Each raised ImportError on its first line, so
`notificationRead` had **never succeeded** -- and the suite was green,
because its tests injected a fake `NotificationType` into
`sys.modules['core.schema'].__dict__` with a comment noting the export was
missing. The bug was mocked, so nothing could see it.

This gate started scoped to `core/schema` on the following reasoning, which
is preserved here because it was wrong in an instructive way:

    a repo-wide version has real false positives. Some modules deliberately
    import an optional dependency inside `try/except ImportError` as step one
    of a documented fallback chain -- `astrolift_drivers.registry` is the
    live example, and flagging it would be wrong.

`astrolift_drivers.registry` is not an optional dependency being probed for.
The module is present and imported unconditionally elsewhere; what fails is
the *name*, on every call, silently. The single example cited as the reason
not to widen the gate turned out to be the first of eleven instances of
exactly what the gate finds (#1614). A detector talked out of running by the
bug it would have caught.

So the gate now covers the trees where that class lives. `KNOWN_BROKEN` holds
what was already broken when it widened, which freezes the count: the
anti-rot test below forces an entry out the moment its site resolves, so the
list can only shrink. Widening further is welcome; do it by adding a
directory and landing whatever it finds in `KNOWN_BROKEN` with a reason.

Scope: this catches a missing module or a missing exported name. It does not
catch the other half of the class, where the module and name resolve and an
*attribute* does not -- #1608's `conn.access_token_ciphertext` was invisible
here. That half still needs behavioural coverage.

Static resolution, no imports executed: this must not depend on Django app
loading, and a gate that fails for an unrelated import-time reason is a gate
people switch off.
"""

from __future__ import annotations

import ast
import pathlib

BACKEND = pathlib.Path(__file__).resolve().parents[2]

# Directories where every first-party import must resolve.
GUARDED = (
    "core/schema",
    "core/serializers",
    "astrolift_pipelines",
    "astrolift_agents",
    "astrolift_workflows",
    "astrolift_operations",
    "astrolift_lifecycle",
)

FIRST_PARTY_PREFIXES = ("core", "config", "astrolift", "providers")

# What was already broken when the gate widened past core/schema (#1614).
# An entry may only be added with a reason, and must go the moment its site
# resolves -- the anti-rot test below enforces that, so this list can only
# shrink. Every one of these is the same shape: a first-party name that does
# not exist, inside a `try` broad enough to swallow the ImportError, so the
# feature above it fails as "not configured" rather than as an error.
#
# The six that lived here before (core.schema.library, core.schema.upload,
# core.schema.user) were repaired in #1593 - the user.py ones together with
# the impersonation gate they were accidentally providing. Two more
# (app_teardown's DeployToken, pipeline_secrets' list_org_secrets) were
# repaired in #1614 rather than listed.
KNOWN_BROKEN: frozenset[str] = frozenset(
    {
        # astrolift_workflows.client has no get_temporal_client; pipeline
        # schedule sync cannot reach Temporal. Tracked in #1614.
        "astrolift_pipelines/schedule_sync.py:54",
        "astrolift_pipelines/schedule_sync.py:119",
        # core.cluster_observability has no get_dynamic_client; pipeline
        # secret plumbing cannot reach the cluster. Tracked in #1614.
        "astrolift_pipelines/secret_plumbing.py:151",
        "astrolift_pipelines/secret_plumbing.py:168",
    }
)


def _module_path(dotted: str) -> pathlib.Path | None:
    for candidate in (
        BACKEND / (dotted.replace(".", "/") + ".py"),
        BACKEND / dotted.replace(".", "/") / "__init__.py",
    ):
        if candidate.exists():
            return candidate
    return None


def _exported_names(dotted: str, _seen: frozenset[str] = frozenset()) -> set[str] | None:
    """Top-level names a module exposes, or None when it cannot be read.

    Star imports are expanded transitively and `__all__` is honoured.
    Without that, every re-export package looks broken: `core.models` is a
    package whose `__init__` star-imports its submodules, so a first draft
    of this check called `from core.models import Tracking` a defect.
    """
    path = _module_path(dotted)
    if path is None or dotted in _seen:
        return None if path is None else set()
    try:
        tree = ast.parse(path.read_text())
    except SyntaxError:  # pragma: no cover - a broken file fails elsewhere
        return None

    names: set[str] = set()
    stars: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
                    if target.id == "__all__" and isinstance(node.value, (ast.List, ast.Tuple)):
                        names.update(
                            e.value
                            for e in node.value.elts
                            if isinstance(e, ast.Constant) and isinstance(e.value, str)
                        )
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name == "*":
                    target_mod = node.module or ""
                    if isinstance(node, ast.ImportFrom) and node.level:
                        base = dotted if path.name == "__init__.py" else dotted.rsplit(".", 1)[0]
                        for _ in range(node.level - 1):
                            base = base.rsplit(".", 1)[0]
                        target_mod = f"{base}.{target_mod}" if target_mod else base
                    stars.append(target_mod)
                else:
                    names.add(alias.asname or alias.name.split(".")[0])

    for star in stars:
        sub = _exported_names(star, _seen | {dotted})
        if sub:
            names |= sub
    return names


def _guarded_files() -> list[pathlib.Path]:
    out: list[pathlib.Path] = []
    for directory in GUARDED:
        for path in sorted((BACKEND / directory).rglob("*.py")):
            if "tests" in path.parts or path.name.startswith("test_"):
                continue
            out.append(path)
    return out


def _break_at(node: ast.ImportFrom) -> str | None:
    """Why this import would raise, or None if it resolves.

    The single rule both tests below apply. They used to carry separate
    logic and the anti-rot one checked only that the module existed, so it
    could never hold an entry for the commoner shape -- module present,
    name absent -- and declared every such entry stale on sight (#1614).
    """
    if not node.module or node.level:
        return None
    root = node.module.split(".")[0]
    if root not in FIRST_PARTY_PREFIXES and not root.startswith("astrolift"):
        return None

    exported = _exported_names(node.module)
    if exported is None:
        return f"no module {node.module!r}"
    for alias in node.names:
        if alias.name == "*" or alias.name in exported:
            continue
        # A submodule is a legitimate `from package import module`.
        if _module_path(f"{node.module}.{alias.name}") is not None:
            continue
        return f"{node.module!r} exports no {alias.name!r}"
    return None


def test_every_first_party_import_in_the_guarded_trees_resolves():
    broken: list[str] = []

    for path in _guarded_files():
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:  # pragma: no cover
            continue
        rel = path.relative_to(BACKEND).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            reason = _break_at(node)
            site = f"{rel}:{node.lineno}"
            if reason and site not in KNOWN_BROKEN:
                broken.append(f"{site}  {reason}")

    assert not broken, (
        "these imports raise ImportError when the line executes:\n  "
        + "\n  ".join(broken)
        + "\n\nA function-local import that cannot resolve fails only when that "
        "path runs, which is why four of these shipped. Resolve the name, or "
        "import from the module that actually defines it."
    )


def test_the_checker_finds_a_planted_break():
    """A structural gate whose matcher stops matching passes over an empty
    set. Prove the rule rejects both shapes, not just the module one."""
    assert _exported_names("core.schema.does.not.exist") is None

    exported = _exported_names("core.schema")
    assert exported is not None
    # The precise fact behind #1590: the package root exports no type names.
    assert "NotificationType" not in exported
    assert "UploadType" not in exported

    missing_module = ast.parse("from core.schema.process import x").body[0]
    assert _break_at(missing_module) == "no module 'core.schema.process'"

    # The shape the anti-rot check used to be blind to: the module is real
    # and the name is not.
    missing_name = ast.parse("from core.schema import NotificationType").body[0]
    assert _break_at(missing_name) == "'core.schema' exports no 'NotificationType'"

    resolves = ast.parse("from core.tests import test_import_resolvability").body[0]
    assert _break_at(resolves) is None


def test_every_known_broken_entry_sits_inside_a_guarded_tree():
    """Coverage cannot be narrowed quietly.

    Nothing else notices GUARDED shrinking: the main test simply reads
    fewer files and passes, and the anti-rot test walks KNOWN_BROKEN
    independently of it. Dropping a directory would silently stop guarding
    the tree while leaving its accepted debt on the books, which reads as
    "still covered". Tying the two together makes a narrowing fail here.
    """
    orphaned = [
        entry
        for entry in sorted(KNOWN_BROKEN)
        if not any(entry.startswith(directory + "/") for directory in GUARDED)
    ]

    assert not orphaned, (
        "these KNOWN_BROKEN entries are outside every guarded directory, so "
        "nothing is checking them any more:\n  " + "\n  ".join(orphaned)
    )


def test_the_known_broken_list_only_holds_still_broken_sites():
    """A ratchet that keeps stale entries stops being a ratchet.

    Once one of these is fixed its entry must go, or the list quietly
    re-permits a future break at the same line.
    """
    stale: list[str] = []

    for entry in sorted(KNOWN_BROKEN):
        rel, _, lineno = entry.rpartition(":")
        path = BACKEND / rel
        if not path.exists():
            stale.append(f"{entry} (file gone)")
            continue
        tree = ast.parse(path.read_text())
        node = next(
            (n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.lineno == int(lineno)),
            None,
        )
        if node is None or not node.module:
            stale.append(f"{entry} (no import on that line any more)")
            continue
        if _break_at(node) is None:
            stale.append(f"{entry} ({node.module!r} resolves now)")

    assert not stale, "these KNOWN_BROKEN entries are no longer broken; remove them:\n  " + "\n  ".join(stale)
