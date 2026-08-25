"""Every first-party module is reachable from an entry point (#1580).

The second class of defect in the orphan sweep: not a broken import, but a
module that imports cleanly, is never imported by anything, and therefore
never runs. `test_import_resolvability.py` catches a name that does not
resolve; this catches a file nothing reaches.

**This is a detector, not a delete list, and the distinction is the whole
reason it exists.** #1580's own method note records the original triage
being run four times as it was corrected -- 87, then 405, then 179, then 132
candidates -- and the 405-candidate run flagged the EKS driver the entire
product runs on. That triage then lived in a scratchpad and was never
committed, so its 45-module DELETE bucket and its 35 duplicate pairings
cannot be checked by anyone today, and the epic has been static since.

Committing the check is what makes the finding outlive the run that found
it. So the contract here is the same one `test_import_resolvability.py`
earned: `KNOWN_UNREACHABLE` freezes today's count, the anti-rot test forces
an entry out the moment its module becomes reachable, and the list can only
shrink. Removing a module is then an ordinary reviewed change that makes
this list shorter, rather than a 6,244-line deletion on one detector run's
say-so.

Static, no imports executed. A gate that fails because an unrelated module
raises at import time is a gate people switch off.

**Reachability is deliberately generous.** A false positive here costs
somebody an argument about a module that is actually load-bearing, which is
exactly how the 405 run went wrong, so anything reached by a framework
convention counts as reachable even when no `import` statement names it.
"""

from __future__ import annotations

import ast
import pathlib
from collections import deque

BACKEND = pathlib.Path(__file__).resolve().parents[2]

# Trees this gate reads. Deliberately the app packages plus `core`, not the
# whole checkout: `providers/` is an independently installed package with its
# own entry points (drivers are loaded by plugin slug, never imported by
# name), so its modules are unreachable by construction from here and
# including it would produce hundreds of false positives.
GUARDED = (
    # Every Python package at the backend root, discovered once and written
    # down rather than derived, so adding a tree is a visible change.
    #
    # `config` is first because it is the primary entry-point tree: settings,
    # urls, the merged GraphQL schema. Two rounds of this detector were wrong
    # because trees were missing -- omitting `config` made
    # `core.views_well_known` an orphan when `config/urls.py` imports it by
    # name, and omitting `workflows` made `core.widgets` an orphan when
    # `workflows/admin.py` imports it. A missing tree does not under-report;
    # it invents orphans, which is the dangerous direction.
    #
    # `providers` is the one deliberate omission: an independently installed
    # package whose drivers are loaded by plugin slug and never imported by
    # name, so its modules are unreachable by construction from here and
    # including it would produce hundreds of false positives.
    "config",
    "core",
    "core_logs",
    "auth1",
    "organization",
    "scheduled_task",
    "workflows",
    "astrolift_agents",
    "astrolift_billing",
    "astrolift_ci",
    "astrolift_ci_convert",
    "astrolift_clusters",
    "astrolift_compliance",
    "astrolift_dispatch",
    "astrolift_drivers",
    "astrolift_forms",
    "astrolift_graphql",
    "astrolift_identity",
    "astrolift_lifecycle",
    "astrolift_manifest",
    "astrolift_observability",
    "astrolift_operations",
    "astrolift_pipelines",
    "astrolift_registry",
    "astrolift_scm",
    "astrolift_services",
    "astrolift_workflows",
)

# Modules Django, Strawberry, Temporal or pytest reach by convention rather
# than by an import statement. Each is an entry point in its own right, so
# each seeds the walk.
#
# `migrations` earns its place for a different reason from the rest: the
# migration runner imports them by directory scan, and a migration is
# supposed to stop being referenced once it has run everywhere. Treating one
# as an orphan would be advice to delete applied history.
CONVENTION_ENTRY_POINTS = (
    "__init__",
    "admin",
    "apps",
    "urls",
    "models",
    "settings",
    "signals",
    "tasks",
    "conftest",
    "migrations",
    "management",
    "checks",
    "middleware",
    "routing",
    "consumers",
    "asgi",
    "wsgi",
    "schema",
    # Process entry points. Nothing imports these -- a supervisor runs them
    # -- so without seeding them the Temporal worker looks unreachable and
    # every workflow it registers looks unreachable with it. That was the
    # first run of this detector, and it is the same false-positive family
    # as #1580's 405-candidate run.
    "__main__",
    "worker",
    # Django loads these by name from a template or a settings string, never
    # by an import statement.
    "templatetags",
)

# Modules already unreachable when this gate landed. Empty, and that is a
# real result rather than a placeholder: **nothing in the guarded trees is
# imported by nothing at all.** The orphan gate therefore starts clean and
# stays clean, and the whole of #1580's finding lives in the second check
# below.
#
# An entry may only be added with a reason, and must go the moment its
# module becomes reachable -- the anti-rot test enforces that, so this list
# can only shrink.
KNOWN_UNREACHABLE: frozenset[str] = frozenset(
    {
        # 16 modules imported by nothing and reached by no framework
        # convention. Baselined from this detector's first stable run and
        # reviewed as families, not one by one -- said plainly because the
        # difference matters: this is accepted debt, not a certification that
        # each was individually judged dead. Each needs its own look before
        # deletion, which is the review #1580's scratchpad list never got and
        # the reason it could not be acted on.
        #
        # Views no urls.py routes -- the strongest form of unreachable.
        "core_logs.views",
        "organization.views",
        "workflows.views",
        # Utility modules with no reference anywhere in the tree.
        "core.collections.dates",
        "core.utils.contexlib",
        "core.utils.date_formatter",
        "core.utils.object_utils",
        "core.systems.organization_system",
        "core.systems.security",
        # Test support no test imports. Here rather than in the test-only
        # list by a route worth recording: that check skips `core.testing.*`
        # because existing to be imported by tests is the package's job, and
        # `factories` falls through the exemption because nothing imports it
        # at all. The exemption forgives being reached only by tests, not
        # being reached by nothing.
        "core.testing.factories",
        # Operator / debug helpers, plausibly run by hand rather than
        # imported. Worth confirming for that reason before removal.
        "auth1.auth0configprint",
        "core_logs.utils.profiling",
        # Feature modules with no caller.
        "astrolift_identity.attestation_gate",
        "astrolift_pipelines.concurrency",
        "astrolift_pipelines.dsl_extensions",
        "astrolift_pipelines.runner_security",
    }
)


def _module_name(path: pathlib.Path) -> str:
    rel = path.relative_to(BACKEND).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _all_modules() -> dict[str, pathlib.Path]:
    out: dict[str, pathlib.Path] = {}
    for tree in GUARDED:
        root = BACKEND / tree
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            out[_module_name(path)] = path
    return out


def _imports_from(path: pathlib.Path, module: str) -> set[str]:
    """First-party module names this file imports.

    Both `import a.b.c` and `from a.b import c` are recorded, and for the
    second form the target is recorded as both `a.b` and `a.b.c` because
    only the filesystem says which of the two `c` is.
    """
    try:
        tree = ast.parse(path.read_text())
    except (SyntaxError, UnicodeDecodeError):  # pragma: no cover
        return set()

    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # Relative. Resolve against this module's package.
                base = module if path.name == "__init__.py" else module.rsplit(".", 1)[0]
                for _ in range(node.level - 1):
                    base = base.rsplit(".", 1)[0] if "." in base else base
                target = f"{base}.{node.module}" if node.module else base
            else:
                target = node.module or ""
            if not target:
                continue
            found.add(target)
            for alias in node.names:
                if alias.name != "*":
                    found.add(f"{target}.{alias.name}")
    return found


# This file. Excluded from the string scan below, because the ratchet lists
# in it are themselves sets of dotted module names -- so the detector counted
# its own accepted-debt list as a set of live references and reported every
# entry in it as reachable. The orphan gate and its anti-rot gate then
# disagreed with each other about the same module, which is the only reason
# it was noticed at all.
_SELF = pathlib.Path(__file__).resolve()


def _dotted_strings(path: pathlib.Path) -> set[str]:
    """Module names referenced as a dotted string rather than imported.

    Django settings name classes this way -- `"core.loaders.template.Loader"`
    is in `TEMPLATES`, and nothing imports that module -- and so do task
    names, middleware lists and authentication backends. A detector blind to
    string references calls all of them orphans, which is advice to delete
    live configuration.

    Generous on purpose: any dotted string whose first segment is a guarded
    tree marks every prefix of itself reachable. Over-marking costs a missed
    orphan; under-marking costs a deletion that breaks boot.
    """
    if path.resolve() == _SELF:
        return set()

    try:
        tree = ast.parse(path.read_text())
    except (SyntaxError, UnicodeDecodeError):  # pragma: no cover
        return set()

    # Docstrings are prose, not references. Without this, a test whose
    # docstring names `core.testing.factories` while explaining why nothing
    # imports it makes the module read as imported -- which is how the two
    # gates here first disagreed with each other about that exact module.
    docstrings = {
        id(n.body[0].value)
        for n in ast.walk(tree)
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and n.body
        and isinstance(n.body[0], ast.Expr)
        and isinstance(n.body[0].value, ast.Constant)
        and isinstance(n.body[0].value.value, str)
    }

    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in docstrings:
            continue
        text = node.value
        if "." not in text or " " in text or "/" in text:
            continue
        parts = text.split(".")
        if parts[0] not in GUARDED:
            continue
        for i in range(1, len(parts) + 1):
            found.add(".".join(parts[:i]))
    return found


def _is_test(module: str) -> bool:
    parts = module.split(".")
    return "tests" in parts or parts[-1].startswith("test_")


def _is_entry_point(module: str, path: pathlib.Path) -> bool:
    """Whether a framework or a supervisor reaches this module by convention.

    A package root counts, read off the path rather than the name.
    `_module_name` strips `__init__`, so `astrolift_ci/__init__.py` arrives
    here as the bare name `astrolift_ci` -- and Django imports every
    INSTALLED_APPS package root whether or not any module names it. A first
    draft checked only for an `__init__` path segment in the dotted name and
    therefore called every app package an orphan.
    """
    if path.name == "__init__.py":
        return True
    return any(part in CONVENTION_ENTRY_POINTS for part in module.split("."))


def _reachable(modules: dict[str, pathlib.Path], *, seed_tests: bool = True) -> set[str]:
    """Modules reachable from any entry point, transitively.

    Package `__init__` files pull their whole package along when they
    star-import or re-export, which `_imports_from` already records, so a
    module re-exported through its package root is reachable even though
    nothing names its file.
    """
    seen: set[str] = set()
    queue: deque[str] = deque(m for m, p in modules.items() if _is_entry_point(m, p))
    # pytest collects test modules, so they are entry points in their own
    # right and what they import is reached. Without this, every
    # test-support module (`core.testing.factories`, `core.testing.fakes`)
    # looks unreachable because only test files import it -- and deleting
    # those on that advice would take the suite with them.
    if seed_tests:
        queue.extend(m for m in modules if _is_test(m))

    while queue:
        current = queue.popleft()
        if current in seen:
            continue
        seen.add(current)
        path = modules.get(current)
        if path is None:
            continue
        for target in _imports_from(path, current) | _dotted_strings(path):
            if target in modules and target not in seen:
                queue.append(target)
            # `from pkg.mod import name` where `name` is not a module: the
            # parent is what got imported.
            parent = target.rsplit(".", 1)[0] if "." in target else ""
            if parent in modules and parent not in seen:
                queue.append(parent)
    return seen


def test_every_module_is_reachable_from_an_entry_point():
    modules = _all_modules()
    reachable = _reachable(modules)

    orphans = sorted(
        name
        for name in modules
        if name not in reachable
        and name not in KNOWN_UNREACHABLE
        and "tests" not in name.split(".")
        and not name.split(".")[-1].startswith("test_")
    )

    assert not orphans, (
        "these modules are imported by nothing and reached by no framework "
        "convention, so the code in them never runs:\n  "
        + "\n  ".join(orphans)
        + "\n\nThis is a detector, not a delete list. Wire it to its caller if "
        "it should run; delete it in a reviewed change if it should not; add it "
        "to KNOWN_UNREACHABLE with a reason if neither is true today."
    )


def test_the_detector_finds_a_planted_orphan(tmp_path):
    """A structural gate whose matcher stops matching passes over an empty
    set, which is indistinguishable from a clean tree.

    #1580's history is the argument for this test specifically: four runs,
    three of them wrong, one flagging the driver the product runs on. A
    detector nobody can see failing is a detector nobody should act on.
    """
    modules = {
        "app.apps": tmp_path / "apps.py",
        "app.wired": tmp_path / "wired.py",
        "app.orphan": tmp_path / "orphan.py",
    }
    (tmp_path / "apps.py").write_text("from app.wired import thing\n")
    (tmp_path / "wired.py").write_text("thing = 1\n")
    (tmp_path / "orphan.py").write_text("unreached = 2\n")

    reachable = _reachable(modules)

    assert "app.apps" in reachable, "an entry point must seed the walk"
    assert "app.wired" in reachable, "an imported module must be reached"
    assert "app.orphan" not in reachable, "the detector no longer finds anything"


def test_a_module_reached_only_through_a_package_reexport_counts_as_reachable(tmp_path):
    """The commonest false positive, and the shape that made the 405 run
    wrong: nothing names the file, but its package root re-exports it."""
    modules = {
        "pkg": tmp_path / "__init__.py",
        "pkg.inner": tmp_path / "inner.py",
    }
    (tmp_path / "__init__.py").write_text("from pkg.inner import Thing\n")
    (tmp_path / "inner.py").write_text("class Thing: pass\n")

    assert "pkg.inner" in _reachable(modules)


def test_a_function_local_import_still_counts(tmp_path):
    """Deferred imports are the repo's normal way of breaking cycles, and
    they are real edges. Missing them would call half the app unreachable."""
    modules = {
        "app.apps": tmp_path / "apps.py",
        "app.late": tmp_path / "late.py",
    }
    (tmp_path / "apps.py").write_text("def f():\n    from app.late import x\n    return x\n")
    (tmp_path / "late.py").write_text("x = 1\n")

    assert "app.late" in _reachable(modules)


def test_every_known_unreachable_entry_is_still_unreachable():
    """A ratchet that keeps stale entries stops being a ratchet."""
    modules = _all_modules()
    reachable = _reachable(modules)

    stale = sorted(entry for entry in KNOWN_UNREACHABLE if entry not in modules or entry in reachable)

    assert not stale, (
        "these KNOWN_UNREACHABLE entries are reachable now (or gone); remove "
        "them, or the list quietly re-permits a future orphan at the same "
        "name:\n  " + "\n  ".join(stale)
    )


def test_every_known_unreachable_entry_sits_inside_a_guarded_tree():
    """Coverage cannot be narrowed quietly: dropping a tree from GUARDED
    would stop checking it while leaving its accepted debt on the books,
    which reads as "still covered"."""
    orphaned = sorted(
        entry
        for entry in KNOWN_UNREACHABLE
        if not any(entry == tree or entry.startswith(tree + ".") for tree in GUARDED)
    )

    assert not orphaned, (
        "these KNOWN_UNREACHABLE entries are outside every guarded tree, so "
        "nothing is checking them:\n  " + "\n  ".join(orphaned)
    )


KNOWN_TEST_ONLY: frozenset[str] = frozenset(
    {
        # The finding: 53 modules whose only route in is a test.
        #
        # Baselined from this detector's first stable run, reviewed as
        # families rather than one by one, same caveat as KNOWN_UNREACHABLE.
        #
        # `core.cluster_observability_eviction` is #1602 step 4, the resolver.
        # Same deliberate kind: the sweep that calls it is step 5. It leaves
        # with the sweep, alongside `astrolift_operations.retention_holds`.
        "core.cluster_observability_eviction",
        # `astrolift_operations.retention_holds` is the third of the same
        # deliberate kind: #1602 step 2 landed the hold table and its
        # projection onto the policy module, while the sweep that calls
        # `active_holds_for` is step 4. Holds ship first on purpose --
        # eviction arriving before them would delete data an incident is
        # depending on with nothing available to stop it. It leaves when the
        # sweep lands.
        "astrolift_operations.retention_holds",
        # Two entries are legitimate and expected to stay. `core.migration_gate`
        # is a gate a test calls by design. `core.domain_handoff` is #1631's
        # handoff token, landed with its suite while the endpoint that calls
        # it waits on two design questions on that issue; it leaves when the
        # exchange endpoint lands.
        #
        # The rest are the defect. The reason to trust the number is that
        # several corroborate work reached from other directions:
        # `astrolift_lifecycle.preview_managed_services` is #1578's subject,
        # and `astrolift_lifecycle.drift` is the config-drift banner whose
        # signals had never fired.
        "astrolift_ci_convert.policy",
        "astrolift_clusters.delivery",
        "astrolift_clusters.emit_all",
        "astrolift_clusters.gitops_emitter",
        "astrolift_clusters.gitops_events",
        "astrolift_clusters.gitops_pr_mode",
        "astrolift_clusters.service_mesh",
        "astrolift_compliance.templates",
        "astrolift_drivers.primitives",
        "astrolift_drivers.storage_catalog",
        "astrolift_drivers.storage_preflight",
        "astrolift_drivers.version_upgrade",
        "astrolift_drivers.volume_snapshot",
        "astrolift_identity.auth_schemes",
        "astrolift_identity.oidc_federation",
        "astrolift_identity.org_subdomain",
        "astrolift_identity.scim_tokens",
        "astrolift_lifecycle.drift",
        "astrolift_lifecycle.preview_managed_services",
        "astrolift_manifest.env_overrides",
        "astrolift_manifest.portability",
        "astrolift_manifest.portability_surfacing",
        "astrolift_operations.llm_usage",
        "astrolift_operations.metrics_logs_api",
        "astrolift_operations.synthetic_checks",
        "astrolift_operations.trace_explorer",
        "astrolift_operations.zentinelle_integration",
        "astrolift_pipelines.context_eval",
        "astrolift_pipelines.pipeline_secrets",
        "astrolift_pipelines.views",
        "astrolift_registry.app_claiming",
        "astrolift_scm.services.trigger_tokens",
        "astrolift_workflows.activities.secret_materialization",
        "astrolift_workflows.app_migration",
        "astrolift_workflows.command_run_output",
        "astrolift_workflows.federation",
        "astrolift_workflows.managed_service_lifecycle",
        "astrolift_workflows.preview_state",
        "astrolift_workflows.sbom_multiarch",
        "astrolift_workflows.utility_workflows",
        "astrolift_workflows.webhook_delivery_history",
        "auth1.session_cookies",
        "auth1.session_tokens",
        "core.anonymization",
        "core.api_versioning",
        "core.cloud_tags",
        "core.collections.bag",
        "core.collections.collectors",
        "core.collections.histogram",
        "core.domain_handoff",
        "core.migration_gate",
        "core.secrets.envelope",
        "core.secrets.rotation",
    }
)


def test_no_module_is_reached_only_by_its_own_tests():
    """The "wired but dead" half of #1580, and the reason it needs its own
    check.

    A module nothing imports at all is visibly dead. A module imported only
    by its test suite looks alive from every angle that usually gets
    checked: it has coverage, the coverage is green, and CI is happy. The
    production call never happens.

    That is strictly worse than an orphan, because the test is what stops
    anyone noticing -- the same shape as #1590, where `notificationRead` had
    never once succeeded and its suite passed by injecting the missing
    export into `sys.modules`. The bug was mocked, so nothing could see it.

    Seeding tests as entry points is still right for the orphan check
    above: without it every test-support module (`core.testing.factories`)
    reads as dead, and deleting those on that advice takes the suite with
    them. So the two questions get two walks, and the difference between
    them is this list.
    """
    modules = _all_modules()
    with_tests = _reachable(modules)
    without_tests = _reachable(modules, seed_tests=False)

    test_only = sorted(
        name
        for name in with_tests - without_tests
        if not _is_test(name)
        # `core.testing.*` exists to be imported by tests. Being reachable
        # only from them is the job, not a defect.
        and not name.startswith("core.testing")
        and name not in KNOWN_TEST_ONLY
    )

    assert not test_only, (
        "these modules are imported by their tests and by nothing else, so "
        "they are covered, green, and never called in production:\n  "
        + "\n  ".join(test_only)
        + "\n\nWire it to its caller, or delete it and its suite together. "
        "Adding it to KNOWN_TEST_ONLY is only honest when the caller is "
        "tracked somewhere -- a passing suite over code nothing runs is the "
        "defect, not the mitigation."
    )


def test_the_test_only_walk_actually_differs_from_the_other_one(tmp_path):
    """Both checks call the same walk, so a refactor that ignored
    `seed_tests` would make the whole second check vacuous: it would compare
    a set with itself, find nothing, and pass forever."""
    (tmp_path / "apps.py").write_text("x = 1\n")
    (tmp_path / "test_thing.py").write_text("from app.only_tested import y\n")
    (tmp_path / "only_tested.py").write_text("y = 2\n")
    modules = {
        "app.apps": tmp_path / "apps.py",
        "app.tests.test_thing": tmp_path / "test_thing.py",
        "app.only_tested": tmp_path / "only_tested.py",
    }

    with_tests = _reachable(modules)
    without_tests = _reachable(modules, seed_tests=False)

    assert "app.only_tested" in with_tests
    assert "app.only_tested" not in without_tests, "seed_tests=False is being ignored"


def test_every_known_test_only_entry_is_still_test_only():
    """Same ratchet as the orphan list: an entry must go the moment its
    module gains a real caller, or the list re-permits a future one."""
    modules = _all_modules()
    with_tests = _reachable(modules)
    without_tests = _reachable(modules, seed_tests=False)
    test_only = with_tests - without_tests

    stale = sorted(entry for entry in KNOWN_TEST_ONLY if entry not in modules or entry not in test_only)

    assert not stale, (
        "these KNOWN_TEST_ONLY entries have a real caller now (or are gone); "
        "remove them:\n  " + "\n  ".join(stale)
    )
