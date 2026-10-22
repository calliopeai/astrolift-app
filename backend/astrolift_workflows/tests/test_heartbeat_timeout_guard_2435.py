"""Activities that heartbeat are scheduled with a heartbeat timeout (#2435).

A worker killed outright reports nothing, so Temporal retries its
activities only when ``heartbeat_timeout`` lapses, or, without one, at
``start_to_close_timeout``. The timeout is only safe when the activity beats
more often than that for as long as it runs, so the guard holds both halves,
statically, over every ``execute_activity`` / ``start_activity`` call in the
workflow modules:

1. A call that schedules a heartbeating activity passes ``heartbeat_timeout``.
2. A call that passes ``heartbeat_timeout`` schedules only heartbeating
   activities. A timeout on an activity that never beats kills it.
3. A scheduled heartbeating activity beats through ``heartbeat_while``,
   awaits nothing outside it, and its callers allow at least
   ``HEARTBEAT_TIMEOUT``, which holds four of the pump's beats. An activity
   that paces its own beats is listed in ``SELF_PACED`` with the reason they
   are frequent enough and the smallest timeout that reason covers.

An activity heartbeats when its body, or a function in its module that it
calls, calls ``activity.heartbeat``, ``heartbeat_while``, ``heartbeat_sink``
or ``maybe_heartbeat``. Beats inside a provider driver do not count: from
``sync_to_async`` they never reach the server (see ``activity_heartbeat``).

A call whose activity the guard cannot name fails as unresolvable: pass the
activity itself, its registered name, a loop over a literal tuple of them, or
a method parameter that callers in the same class fill with one.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import pathlib
from datetime import timedelta
from functools import cache

from astrolift_workflows.activity_heartbeat import HEARTBEAT_EVERY, HEARTBEAT_TIMEOUT
from astrolift_workflows.worker import ACTIVITIES

WORKFLOWS = pathlib.Path("astrolift_workflows/workflows")
SCHEDULERS = {"execute_activity", "start_activity"}
BEATS = {"heartbeat", "heartbeat_sink", "maybe_heartbeat"}
PUMP = "heartbeat_while"

#: Registered name -> (smallest heartbeat_timeout its callers may pass, why).
SELF_PACED: dict[str, tuple[timedelta, str]] = {
    "astrolift.agent.dispatch_task": (
        timedelta(minutes=2),
        "Beats before each status poll, every 10 s plus the poll. The beats mean "
        "the poll loop is advancing; a beat on a timer would hide a hung poll "
        "until start_to_close_timeout, 24 hours or more.",
    ),
    "astrolift.app.configure_native_endpoint": (
        timedelta(seconds=30),
        "A pulse thread beats every second for the whole native configuration "
        "and refuses to start without a heartbeat timeout of its own (#2278).",
    ),
    "astrolift.domain_app_route.reconcile": (
        timedelta(seconds=20),
        "Beats at each admission checkpoint of the reconcile, before every "
        "external effect. The 20 s timeout bounds the time between checkpoints, "
        "which a beat on a timer would not.",
    ),
}


def _definition_name(fn) -> str:
    return fn.__temporal_activity_definition.name


REGISTRY = {_definition_name(fn): fn for fn in ACTIVITIES}


def _called(node: ast.AST) -> set[str]:
    names = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            func = sub.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


@cache
def _module_functions(module_name: str) -> dict[str, ast.AST]:
    source = inspect.getsource(importlib.import_module(module_name))
    return {
        node.name: node
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def _beat_kinds(module_name: str, function: str, seen: frozenset[str] = frozenset()) -> set[str]:
    """``{"pump"}`` and/or ``{"beat"}``: how a function, or what it calls in its module, beats."""
    functions = _module_functions(module_name)
    called = _called(functions[function])
    kinds = {"pump"} if PUMP in called else set()
    if called & BEATS:
        kinds.add("beat")
    for callee in (called & functions.keys()) - seen - {function}:
        kinds |= _beat_kinds(module_name, callee, seen | {function})
    return kinds


def _kinds(definition: str) -> set[str]:
    fn = REGISTRY[definition]
    return _beat_kinds(fn.__module__, fn.__name__)


def _outside_the_pump(function: ast.AST):
    """The nodes of ``function`` that run outside ``heartbeat_while``.

    Skips the pump's argument, which is the pumped work, and nested functions,
    which run only where they are called.
    """
    stack = list(ast.iter_child_nodes(function))
    while stack:
        node = stack.pop()
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            continue
        yield node
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == PUMP:
            continue
        stack.extend(ast.iter_child_nodes(node))


def _unpumped_awaits(module_name: str, function: str, seen: frozenset[str] = frozenset()) -> list[str]:
    """Each await in a function, or in what it calls in its module, that the pump does not cover."""
    functions = _module_functions(module_name)
    gaps, helpers = [], set()
    for node in _outside_the_pump(functions[function]):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in functions:
            helpers.add(node.func.id)
        if isinstance(node, ast.Await):
            call = node.value
            # Awaiting the pump, or a helper in the module, which is checked as the activity's own code.
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name):
                if call.func.id == PUMP or call.func.id in functions:
                    continue
            gaps.append(f"{module_name}:{node.lineno} await {ast.unparse(call)}")
    for helper in helpers - seen - {function}:
        gaps += _unpumped_awaits(module_name, helper, seen | {function})
    return gaps


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}


def _enclosing(node, parents, kinds):
    while node in parents:
        node = parents[node]
        if isinstance(node, kinds):
            yield node


def _resolve(expr, at, module, parents) -> set[str]:
    """The registered names of every activity ``expr`` can be at this call."""
    if isinstance(expr, ast.Constant) and expr.value in REGISTRY:
        return {expr.value}
    if isinstance(expr, ast.IfExp):
        return _resolve(expr.body, at, module, parents) | _resolve(expr.orelse, at, module, parents)
    if not isinstance(expr, ast.Name):
        raise LookupError(ast.unparse(expr))
    for loop in _enclosing(at, parents, ast.For | ast.AsyncFor):
        bound = _loop_values(loop, expr.id)
        if bound is not None:
            return set().union(*(_resolve(value, loop, module, parents) for value in bound))
    for function in _enclosing(at, parents, ast.FunctionDef | ast.AsyncFunctionDef):
        params = [a.arg for a in function.args.args]
        if expr.id in params:
            return _resolve_parameter(function, params.index(expr.id), module, parents)
        break
    fn = getattr(module, expr.id, None)
    if hasattr(fn, "__temporal_activity_definition"):
        return {_definition_name(fn)}
    raise LookupError(expr.id)


def _loop_values(loop, name):
    """What ``name`` takes in ``for ... in (<literal>)``, or None if the loop does not bind it."""
    target, values = loop.target, loop.iter
    if isinstance(target, ast.Name) and target.id == name:
        position = None
    elif isinstance(target, ast.Tuple) and any(isinstance(t, ast.Name) and t.id == name for t in target.elts):
        position = next(i for i, t in enumerate(target.elts) if isinstance(t, ast.Name) and t.id == name)
    else:
        return None
    if not isinstance(values, ast.Tuple | ast.List):
        raise LookupError(f"{name} in {ast.unparse(values)}")
    return values.elts if position is None else [item.elts[position] for item in values.elts]


def _resolve_parameter(function, index, module, parents) -> set[str]:
    """A method parameter: whatever ``self.<method>(...)`` callers in its class pass."""
    owner = next(_enclosing(function, parents, ast.ClassDef), None)
    if owner is None or index == 0:
        raise LookupError(function.name)
    found: set[str] = set()
    callers = [
        call
        for call in ast.walk(owner)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == function.name
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "self"
    ]
    if not callers:
        raise LookupError(function.name)
    for call in callers:
        found |= _resolve(call.args[index - 1], call, module, parents)
    return found


def _call_sites():
    """``(where, keywords, activities, module)`` for every scheduling call."""
    for path in sorted(WORKFLOWS.glob("*.py")):
        module = importlib.import_module(f"astrolift_workflows.workflows.{path.stem}")
        tree = ast.parse(path.read_text())
        parents = _parents(tree)
        for call in ast.walk(tree):
            if not (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr in SCHEDULERS
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "workflow"
            ):
                continue
            where = f"{path}:{call.lineno}"
            keywords = {kw.arg: kw.value for kw in call.keywords}
            try:
                activities = _resolve(call.args[0], call, module, parents)
            except (IndexError, LookupError) as exc:
                activities = {f"<unresolvable: {exc}>"}
            yield where, keywords, activities, module


SITES = list(_call_sites())


def _beats(definition: str) -> bool:
    return definition in REGISTRY and bool(_kinds(definition))


def test_every_call_site_resolves_to_registered_activities():
    unknown = {where: sorted(acts) for where, _, acts, _ in SITES if not acts <= REGISTRY.keys()}

    assert unknown == {}


def test_a_heartbeating_activity_is_always_scheduled_with_a_heartbeat_timeout():
    missing = {
        where: sorted(acts)
        for where, keywords, acts, _ in SITES
        if any(_beats(a) for a in acts) and "heartbeat_timeout" not in keywords
    }

    assert missing == {}, (
        "these calls schedule an activity that heartbeats but set no heartbeat_timeout, "
        "so a killed worker strands it until start_to_close_timeout: pass "
        f"heartbeat_timeout=HEARTBEAT_TIMEOUT. {missing}"
    )


def test_a_heartbeat_timeout_only_covers_activities_that_beat():
    silent = {
        where: sorted(a for a in acts if not _beats(a))
        for where, keywords, acts, _ in SITES
        if "heartbeat_timeout" in keywords and not all(_beats(a) for a in acts)
    }

    assert silent == {}, (
        "these calls set heartbeat_timeout on an activity that never beats, which "
        f"Temporal kills once the timeout passes: wrap its work in heartbeat_while. {silent}"
    )


def test_each_heartbeat_timeout_is_comfortably_above_the_beat_interval():
    """Pumped activities beat every 30 s; callers allow four beats."""
    short = {}
    for where, keywords, acts, module in SITES:
        if "heartbeat_timeout" not in keywords:
            continue
        expression = ast.Expression(keywords["heartbeat_timeout"])
        # The workflow module's own expression, such as HEARTBEAT_TIMEOUT.
        allowed = eval(compile(expression, where, "eval"), vars(module))
        for definition in acts:
            floor = SELF_PACED[definition][0] if definition in SELF_PACED else HEARTBEAT_TIMEOUT
            if allowed < floor:
                short[f"{where} {definition}"] = f"{allowed} < {floor}"

    assert short == {}


def test_the_pump_beats_four_times_inside_the_timeout_callers_pass():
    """The check above measures callers against ``HEARTBEAT_TIMEOUT``, so the
    constant itself must hold four pumped beats. The SDK forwards at most one
    beat per min(80% of the timeout, 60 s), the worker's default throttle cap,
    which leaves the server two of them."""
    forwarded_every = max(HEARTBEAT_EVERY, min(HEARTBEAT_TIMEOUT * 0.8, timedelta(seconds=60)))

    assert HEARTBEAT_EVERY * 4 <= HEARTBEAT_TIMEOUT
    assert forwarded_every * 2 <= HEARTBEAT_TIMEOUT


def test_a_scheduled_heartbeating_activity_beats_on_a_timer_or_is_self_paced():
    scheduled = {a for _, _, acts, _ in SITES for a in acts if _beats(a)}
    unpaced = sorted(a for a in scheduled if "pump" not in _kinds(a) and a not in SELF_PACED)

    assert unpaced == [], (
        "these activities heartbeat only at fixed points, so a long call between "
        "two beats outlives any heartbeat_timeout: await the work through "
        f"heartbeat_while, or add them to SELF_PACED with the reason. {unpaced}"
    )
    assert sorted(SELF_PACED.keys() - scheduled) == [], "SELF_PACED names an activity no workflow schedules"


def test_a_pumped_activity_awaits_nothing_outside_the_pump():
    """The pump beats only while its own work runs. Any other await in the
    activity goes without a beat for as long as it waits, which for a
    ``sync_to_async`` call includes the time queued behind another activity's
    call on the worker's shared thread."""
    pumped = {a for _, _, acts, _ in SITES for a in acts if _beats(a)} - SELF_PACED.keys()
    gaps = {a: _unpumped_awaits(REGISTRY[a].__module__, REGISTRY[a].__name__) for a in sorted(pumped)}
    # Agent dispatch paces its own beats around plain awaits, so the check must see them.
    dispatch = REGISTRY["astrolift.agent.dispatch_task"]

    assert {a: g for a, g in gaps.items() if g} == {}, "await these through heartbeat_while"
    assert _unpumped_awaits(dispatch.__module__, dispatch.__name__)


def test_the_guard_sees_every_shape_of_activity_reference():
    """The resolver follows the dynamic references the workflows really use."""
    by_file = {}
    for where, _, acts, _ in SITES:
        by_file.setdefault(where.split(":")[0].rsplit("/", 1)[-1], set()).update(acts)

    assert "astrolift.command_run.assert_no_concurrent" in by_file["command_run.py"]
    assert {
        "astrolift.brain_create.repo",
        "astrolift.brain_create.observe",
    } <= by_file["brain_create.py"]
    assert "astrolift.cluster.cleanup_acm_certs" in by_file["decommission_cluster.py"]
    assert {
        "astrolift.app.expiry_retire_runtime",
        "astrolift.app.delete_namespaces",
    } <= by_file["deregister_app.py"]
