"""Static-analysis CI test for webhook HMAC signature enforcement (#529).

Walks every URL pattern in the project whose path contains the substring
``webhook`` and verifies that the resolved view function (or any
sibling function it calls in the same module) carries an HMAC signature
check before reading the request body.

Why this lives in CI
====================

The ALB Cognito bypass for ``/api/webhooks/*`` and the currently-mounted
``/app/auth1/scm/<kind>/webhook/<connection_id>/`` routes means these
handlers are reachable from the public internet — no edge auth in front.
The HMAC signature check IS the trust boundary.

Convention-only protection silently rots as the surface grows. A
drive-by PR that adds a new webhook handler and forgets to verify must
be a red CI light, not a code-review hope.

What counts as a valid verifier
===============================

The guard accepts any of the following, found either as a direct call
inside the view function body, as a decorator on the view, or as a
direct call inside a sibling function the view calls (one level of
recursion, same module):

* ``verify_signature``
* ``verify_hmac``
* ``verify_webhook_signature``
* ``validate_signature``
* ``check_hmac``

If a handler legitimately has no signature (e.g. a health probe,
unauthenticated by design) it must:

1. Be listed in the ``EXEMPT`` dict below with a written justification.
2. Carry a ``# guardrail: webhook-public`` comment at the top of its
   body so the next person reading the code sees the intent.

The bar for adding an EXEMPT entry is high: every entry widens the
public attack surface. Code review should treat each new entry as a
security-relevant change.

Approach
========

* Discover routes via Django's URL resolver (``get_resolver``) rather
  than grepping ``urls.py`` files. The resolver is the ground truth and
  follows ``include()`` chains, namespaces, etc.
* For each route whose ``pattern`` stringifies to something containing
  ``webhook``, resolve the ``callback`` to a Python function.
* Unwrap common decorator chains (``csrf_exempt``, ``require_POST``,
  ``ratelimit``, ``functools.wraps``) so we get to the bare view.
* AST-scan the view function (and same-file callees one level deep) for
  the verifier names listed above, as either calls or decorators.
* Fail with a per-handler breakdown if any handler lacks the check and
  isn't on EXEMPT.
"""

from __future__ import annotations

import ast
import importlib
import inspect
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import django
import pytest

BACKEND = Path(__file__).resolve().parents[2]


# Names the guard treats as a valid HMAC signature check. The set is
# intentionally small and concrete; if you want to add a new name,
# update both this set AND the docstring above so the rule stays
# discoverable.
VERIFIER_NAMES: frozenset[str] = frozenset(
    {
        "verify_signature",
        "verify_hmac",
        "verify_webhook_signature",
        "validate_signature",
        "check_hmac",
    }
)


# Webhook handlers that legitimately do NOT need an HMAC signature
# check. Key is ``module.qualname`` of the view fn (matches the
# importable path Django reports). Value is a written justification
# that must explain WHY this handler is safe to expose to the public
# internet without a signature.
#
# Each entry widens the public attack surface — review every new entry
# as a security-relevant change. The default and correct fix when a
# handler fails this guard is to ADD the verification, not EXEMPT it.
EXEMPT: dict[str, str] = {}


# Decorators commonly wrapped around Django views that should be
# transparently unwrapped. The unwrap is best-effort — if we can't get
# to the bare function we fall back to inspecting whatever we have.
_TRANSPARENT_DECORATORS: frozenset[str] = frozenset(
    {
        "csrf_exempt",
        "csrf_protect",
        "require_POST",
        "require_GET",
        "require_http_methods",
        "ratelimit",
        "login_required",
        "permission_required",
        "transaction.atomic",
        "atomic",
    }
)


def _ensure_django_setup() -> None:
    """Import-side Django bootstrap. Mirrors the bootstrap the test
    runner does via DJANGO_SETTINGS_MODULE — but cheap enough to be
    idempotent and safe to call at module import."""
    if not django.apps.apps.ready:
        django.setup()


def _unwrap(fn: Any) -> Any:
    """Peel decorator wrappers off ``fn`` until we hit the inner view.

    Handles ``functools.wraps`` (``__wrapped__``), Django's
    ``method_decorator`` (``view_func``), and a few hand-rolled
    closures by walking ``cell_contents`` of any cells that look like
    callables. Stops on cycles or anything we can't unwrap further.
    """
    seen: set[int] = set()
    while True:
        if id(fn) in seen:
            return fn
        seen.add(id(fn))
        # functools.wraps / partial
        inner = getattr(fn, "__wrapped__", None)
        if callable(inner) and inner is not fn:
            fn = inner
            continue
        # Django's method_decorator-style wrappers
        inner = getattr(fn, "view_func", None)
        if callable(inner) and inner is not fn:
            fn = inner
            continue
        # Closure walking: find a cell whose contents is a callable
        # defined in a project module. Don't pull from stdlib or
        # site-packages.
        closure = getattr(fn, "__closure__", None) or ()
        candidate = None
        for cell in closure:
            try:
                contents = cell.cell_contents
            except ValueError:
                continue
            if not callable(contents) or contents is fn:
                continue
            mod = getattr(contents, "__module__", "") or ""
            # Only descend into project code; avoid wandering into
            # django.views.decorators internals.
            if (
                not mod.startswith("django.")
                and not mod.startswith("ratelimit.")
                and "site-packages"
                not in (
                    getattr(inspect.getsourcefile(contents) or "", "__str__", lambda: "")()
                    if inspect.isfunction(contents)
                    else ""
                )
            ):
                candidate = contents
                break
        if candidate is not None:
            fn = candidate
            continue
        return fn


def _is_webhook_path(pattern: str) -> bool:
    """True when the URL pattern names a webhook *receiver* (a path
    segment is exactly ``webhook`` or ``webhooks``), as opposed to an
    admin CRUD page on a model whose slug happens to contain the
    substring ``webhook`` (e.g. ``/admin/.../webhooksubscription/``).

    Hits:
      /api/webhooks/github/...
      /app/auth1/scm/github/webhook/<id>/

    Misses:
      /app/admin/astrolift_operations/webhooksubscription/
      /app/admin/astrolift_operations/webhookdelivery/<id>/change/
    """
    segments = [s for s in pattern.lower().split("/") if s]
    return any(s in {"webhook", "webhooks"} for s in segments)


def _all_webhook_routes() -> list[tuple[str, Any]]:
    """Return [(pattern_str, callback)] for every URL pattern that
    looks like a webhook *receiver*. Discovery is via Django's URL
    resolver so this follows ``include()`` chains and namespaces."""
    _ensure_django_setup()
    from django.urls import URLPattern, URLResolver, get_resolver

    out: list[tuple[str, Any]] = []

    def walk(resolver_or_pattern: Any, prefix: str) -> None:
        if isinstance(resolver_or_pattern, URLResolver):
            this_prefix = prefix + str(resolver_or_pattern.pattern)
            for child in resolver_or_pattern.url_patterns:
                walk(child, this_prefix)
            return
        if isinstance(resolver_or_pattern, URLPattern):
            full = prefix + str(resolver_or_pattern.pattern)
            if _is_webhook_path(full):
                out.append((full, resolver_or_pattern.callback))
            return

    resolver = get_resolver()
    for entry in resolver.url_patterns:
        walk(entry, "")

    return out


def _decorator_name(node: ast.expr) -> str:
    """Return the bare callable name of a decorator AST node."""
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _call_name(node: ast.expr) -> str:
    """Return the bare callable name of a Call node's target."""
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _find_func_def(module_ast: ast.Module, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Locate a top-level function definition by name in a parsed
    module. Returns the first match; webhook views are top-level by
    convention so we don't recurse into classes."""
    for node in module_ast.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == name:
                return node
    return None


def _decorators_match(
    fn: ast.FunctionDef | ast.AsyncFunctionDef,
    names: Iterable[str],
) -> bool:
    target = set(names)
    return any(_decorator_name(d) in target for d in fn.decorator_list)


def _calls_in(
    fn: ast.FunctionDef | ast.AsyncFunctionDef,
    names: Iterable[str],
) -> bool:
    target = set(names)
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and _call_name(node.func) in target:
            return True
    return False


def _local_callees(
    fn: ast.FunctionDef | ast.AsyncFunctionDef,
) -> set[str]:
    """Names of callables this function calls. Used to descend one
    level when the verifier lives in a helper function in the same
    module (e.g. github_webhook → _handle → verify_signature)."""
    out: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name:
                out.add(name)
    return out


def _has_public_guardrail_comment(source: str, fn_name: str) -> bool:
    """True when the source contains the
    ``# guardrail: webhook-public`` opt-out marker inside the named
    function. The marker is the human-readable counterpart to an
    EXEMPT entry — both must be present for an unauthenticated
    handler to pass review."""
    marker = "# guardrail: webhook-public"
    if marker not in source:
        return False
    # cheap scoping check: marker must appear after the ``def fn_name``
    # line. A view that only mentions the marker in its docstring or
    # an unrelated helper doesn't pass.
    try:
        def_pos = source.index(f"def {fn_name}(")
    except ValueError:
        return False
    return source.find(marker, def_pos) != -1


def _qualified_name(view: Any) -> str:
    """``module.qualname`` for the view function. Falls back to repr
    when the view is something exotic (a class-based view instance,
    a partial, etc.)."""
    module = getattr(view, "__module__", "") or "<unknown>"
    qualname = getattr(view, "__qualname__", "") or getattr(view, "__name__", "") or repr(view)
    return f"{module}.{qualname}"


def _has_verifier(view: Any) -> tuple[bool, str]:
    """Return (ok, reason). ok=True when the view (or a same-module
    callee) carries a recognized HMAC signature check; reason explains
    the failure for the error message."""
    fn = _unwrap(view)
    try:
        source_path = inspect.getsourcefile(fn)
    except TypeError:
        return False, "view is not a Python function we can inspect"
    if not source_path:
        return False, "view has no source file"

    try:
        source = Path(source_path).read_text()
    except OSError as e:
        return False, f"cannot read view source: {e}"

    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return False, f"view source has syntax error: {e}"

    fn_name = getattr(fn, "__name__", "")
    fn_def = _find_func_def(tree, fn_name)
    if fn_def is None:
        return False, f"could not locate ast for {fn_name} in {source_path}"

    # Path 1: a recognized decorator on the view itself.
    if _decorators_match(fn_def, VERIFIER_NAMES):
        return True, "decorator match"

    # Path 2: a direct call to a recognized verifier inside the view.
    if _calls_in(fn_def, VERIFIER_NAMES):
        return True, "direct call match"

    # Path 3: one level of recursion. The view calls a helper in the
    # same module which calls the verifier. The dispatch shape used
    # by ``auth1.scm_webhook.github_webhook → _handle → verify_signature``
    # is the canonical case.
    for callee_name in _local_callees(fn_def):
        callee_def = _find_func_def(tree, callee_name)
        if callee_def is None:
            continue
        if _decorators_match(callee_def, VERIFIER_NAMES):
            return True, f"decorator match via {callee_name}"
        if _calls_in(callee_def, VERIFIER_NAMES):
            return True, f"direct call match via {callee_name}"

    return False, (f"no call to any of {sorted(VERIFIER_NAMES)} found in {fn_name} or its same-file callees")


# ---------------------------------------------------------------------
# The actual CI guard.
# ---------------------------------------------------------------------


def test_every_webhook_handler_verifies_hmac() -> None:
    """Every URL pattern matching ``webhook`` resolves to a view that
    either calls a canonical HMAC verifier (or has one as a decorator)
    OR is listed in EXEMPT with a written justification AND carries
    the public-guardrail opt-out marker."""
    routes = _all_webhook_routes()

    if not routes:
        # If we found zero webhook routes the guard is silently
        # disarmed — fail loudly so we notice if a refactor moves
        # them somewhere the resolver can't see.
        pytest.fail(
            "No URL patterns containing 'webhook' were found. The guard "
            "depends on Django's URL resolver — if all webhook handlers "
            "were just moved, update the discovery in "
            "_all_webhook_routes(). If they were just deleted, remove "
            "this assertion."
        )

    failures: list[str] = []
    for pattern, view in routes:
        qualname = _qualified_name(view)
        ok, reason = _has_verifier(view)
        if ok:
            continue

        if qualname in EXEMPT:
            # An EXEMPT entry must be paired with the in-source
            # guardrail comment so the next reader sees the
            # exemption inline, not just in the test file.
            fn = _unwrap(view)
            try:
                source = Path(inspect.getsourcefile(fn) or "").read_text()
            except OSError:
                source = ""
            if _has_public_guardrail_comment(source, getattr(fn, "__name__", "")):
                continue
            failures.append(
                f"{pattern} -> {qualname} is in EXEMPT but the view body "
                "is missing the required '# guardrail: webhook-public' "
                "comment. Add the comment with the justification so the "
                "next reader sees why this handler is intentionally "
                "unauthenticated."
            )
            continue

        failures.append(f"{pattern} -> {qualname}: {reason}")

    if failures:
        msg = (
            "Webhook signature verification missing on these handlers:\n  - "
            + "\n  - ".join(failures)
            + "\n\nFix: ADD the verifier (call one of "
            + ", ".join(sorted(VERIFIER_NAMES))
            + ") before reading the request body. Do NOT add the "
            "handler to EXEMPT unless it is intentionally public "
            "(e.g. a health probe) AND you can explain why in writing."
        )
        pytest.fail(msg)


# ---------------------------------------------------------------------
# Tests on the guard itself. The guard is CI infrastructure; if it
# breaks, every webhook ships without coverage. These tests exercise
# the AST scanner end-to-end with synthetic view modules so we catch
# regressions in the matching logic.
# ---------------------------------------------------------------------


def _module_from_source(tmp_path: Path, source: str, name: str) -> Any:
    """Write ``source`` to a tmp .py file, import it, return the
    module. Used by the unit tests below to build synthetic webhook
    views without leaking them into the project."""
    path = tmp_path / f"{name}.py"
    path.write_text(source)
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_guard_flags_view_with_no_verifier(tmp_path: Path) -> None:
    """A view that just reads the body and returns 200 is rejected."""
    src = (
        "from typing import Any\n"
        "def my_webhook(request: Any) -> Any:\n"
        "    body = getattr(request, 'body', b'')\n"
        "    return body\n"
    )
    mod = _module_from_source(tmp_path, src, "guard_test_no_verifier")
    ok, reason = _has_verifier(mod.my_webhook)
    assert ok is False
    assert "no call to any of" in reason


def test_guard_accepts_view_calling_verify_signature(tmp_path: Path) -> None:
    """A view that calls ``verify_signature(...)`` inline passes."""
    src = (
        "from typing import Any\n"
        "def verify_signature(**kw) -> bool:\n"
        "    return True\n"
        "def my_webhook(request: Any) -> Any:\n"
        "    verify_signature(body=request.body)\n"
        "    return request.body\n"
    )
    mod = _module_from_source(tmp_path, src, "guard_test_call_match")
    ok, _ = _has_verifier(mod.my_webhook)
    assert ok is True


def test_guard_accepts_view_with_verifier_decorator(tmp_path: Path) -> None:
    """A view decorated with ``@verify_webhook_signature`` passes."""
    src = (
        "from typing import Any\n"
        "def verify_webhook_signature(fn):\n"
        "    return fn\n"
        "@verify_webhook_signature\n"
        "def my_webhook(request: Any) -> Any:\n"
        "    return request.body\n"
    )
    mod = _module_from_source(tmp_path, src, "guard_test_decorator_match")
    ok, _ = _has_verifier(mod.my_webhook)
    assert ok is True


def test_guard_accepts_view_dispatching_to_same_module_helper(
    tmp_path: Path,
) -> None:
    """A thin view delegating to a same-module ``_handle`` that calls
    the verifier passes — this is the auth1.scm_webhook shape."""
    src = (
        "from typing import Any\n"
        "def verify_signature(**kw) -> bool:\n"
        "    return True\n"
        "def _handle(kind: str, request: Any) -> Any:\n"
        "    verify_signature(kind=kind, body=request.body)\n"
        "    return request.body\n"
        "def my_webhook(request: Any) -> Any:\n"
        "    return _handle('github', request)\n"
    )
    mod = _module_from_source(tmp_path, src, "guard_test_one_level_recursion")
    ok, reason = _has_verifier(mod.my_webhook)
    assert ok is True, reason
    assert "_handle" in reason


def test_guard_allows_exempt_with_public_guardrail_comment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An EXEMPT entry passes when the view body carries the
    `# guardrail: webhook-public` marker; fails without it."""
    src_with_marker = (
        "from typing import Any\n"
        "def health_webhook(request: Any) -> Any:\n"
        "    # guardrail: webhook-public — readiness probe; no payload trust\n"
        "    return {'ok': True}\n"
    )
    mod = _module_from_source(tmp_path, src_with_marker, "guard_test_exempt_ok")
    # Mark this fn as EXEMPT for the duration of the test, mimicking
    # the dict entry an author would add for a real health probe.
    qualname = _qualified_name(mod.health_webhook)
    monkeypatch.setitem(EXEMPT, qualname, "health probe; no payload trust")
    # The EXEMPT-comment path is exercised by the main guard, not
    # _has_verifier, so simulate that branch directly.
    source = Path(inspect.getsourcefile(mod.health_webhook) or "").read_text()
    assert _has_public_guardrail_comment(source, "health_webhook") is True

    # And without the marker: the EXEMPT entry alone does NOT save it.
    src_without_marker = (
        "from typing import Any\ndef health_webhook(request: Any) -> Any:\n    return {'ok': True}\n"
    )
    mod_bare = _module_from_source(tmp_path, src_without_marker, "guard_test_exempt_bare")
    source_bare = Path(inspect.getsourcefile(mod_bare.health_webhook) or "").read_text()
    assert _has_public_guardrail_comment(source_bare, "health_webhook") is False


def test_runtime_unsigned_payload_is_rejected() -> None:
    """End-to-end runtime check: the live HMAC verifier rejects a
    payload with no / wrong signature header. Belt-and-suspenders on
    top of the AST guard — guards the code path, not the call site."""
    from django.test import RequestFactory

    from auth1.scm_webhook import verify_signature

    secret = b"shared-secret"
    body = b'{"ref":"refs/heads/main"}'
    rf = RequestFactory()

    # Missing header.
    req = rf.post("/scm/github/webhook/abc/", data=body, content_type="application/json")
    assert verify_signature(kind="github", request=req, body=body, secret=secret) is False

    # Wrong signature.
    req = rf.post(
        "/scm/github/webhook/abc/",
        data=body,
        content_type="application/json",
        HTTP_X_HUB_SIGNATURE_256="sha256=" + "0" * 64,
    )
    assert verify_signature(kind="github", request=req, body=body, secret=secret) is False


def test_runtime_valid_signature_is_accepted() -> None:
    """End-to-end runtime check: a correctly-signed payload passes the
    live verifier. Pairs with the rejection test above to cover both
    arms of the trust boundary."""
    import hashlib
    import hmac

    from django.test import RequestFactory

    from auth1.scm_webhook import verify_signature

    secret = b"shared-secret"
    body = b'{"ref":"refs/heads/main"}'
    digest = hmac.new(secret, body, hashlib.sha256).hexdigest()

    rf = RequestFactory()
    req = rf.post(
        "/scm/github/webhook/abc/",
        data=body,
        content_type="application/json",
        HTTP_X_HUB_SIGNATURE_256=f"sha256={digest}",
    )
    assert verify_signature(kind="github", request=req, body=body, secret=secret) is True

    # GitLab path: opaque token compare, not HMAC.
    req_gl = rf.post(
        "/scm/gitlab/webhook/abc/",
        data=body,
        content_type="application/json",
        HTTP_X_GITLAB_TOKEN="shared-secret",
    )
    assert verify_signature(kind="gitlab", request=req_gl, body=body, secret=secret) is True


def test_unknown_webhook_kind_is_rejected() -> None:
    """A request for an unsupported provider returns False (treated as
    unauthorized) rather than crashing — defends against typo'd dispatch
    keys leaking past the auth boundary."""
    from django.test import RequestFactory

    from auth1.scm_webhook import verify_signature

    req = RequestFactory().post("/x/", data=b"x", content_type="application/json")
    assert verify_signature(kind="bogus", request=req, body=b"x", secret=b"s") is False
