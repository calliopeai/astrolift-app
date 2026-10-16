"""``astroliftExecutePromql`` reads only the app environment's namespace (#2418).

The resolver passed the caller's PromQL to the shared cluster Prometheus
verbatim, so ``APP_READ`` on any app read every tenant's metrics. Every
selector now gains the app namespace's matcher, and an expression the
confiner cannot read is refused instead of run.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from astrolift_lifecycle.models import AppEnvironment
from astrolift_observability.promql_scope import PromqlScopeError, confine_to_namespace
from astrolift_observability.schema.queries import GoldenSignalsQuery
from core.cluster_observability import namespace_for_app
from core.permissions import Permission

from .test_execute_promql_750 import _END, _START, _STEP, _ctx, _info, _scaffold

NS = 'namespace="acme-web"'


@pytest.mark.parametrize(
    ("query", "confined"),
    [
        ("up", f"up{{{NS}}}"),
        ('up{job="x"}', f'up{{job="x",{NS}}}'),
        ('up{job="x",}', f'up{{job="x",{NS}}}'),
        ('{__name__=~".+"}', f'{{__name__=~".+",{NS}}}'),
        ('up{namespace="other"}', f'up{{namespace="other",{NS}}}'),
        (
            'sum by (pod) (rate(http_requests_total{code=~"5.."}[5m]))',
            f'sum by (pod) (rate(http_requests_total{{code=~"5..",{NS}}}[5m]))',
        ),
        (
            "sum(rate(x[5m])) by (pod) / on(pod) group_left(node) kube_pod_info",
            f"sum(rate(x{{{NS}}}[5m])) by (pod) / on(pod) group_left(node) kube_pod_info{{{NS}}}",
        ),
        (
            "histogram_quantile(0.99, sum by (le) (rate(h_bucket[5m:1m] offset 1h)))",
            f"histogram_quantile(0.99, sum by (le) (rate(h_bucket{{{NS}}}[5m:1m] offset 1h)))",
        ),
        ("a > bool 3", f"a{{{NS}}} > bool 3"),
        ("a and b unless c", f"a{{{NS}}} and b{{{NS}}} unless c{{{NS}}}"),
        ("x @ start()", f"x{{{NS}}} @ start()"),
        ('label_replace(up, "a", "$1", "b", "(.*)")', f'label_replace(up{{{NS}}}, "a", "$1", "b", "(.*)")'),
        ("job:http:rate5m", f"job:http:rate5m{{{NS}}}"),
        ("vector(1) + time()", "vector(1) + time()"),
        ("NaN + Inf", "NaN + Inf"),
        ('up{a="}{"}', f'up{{a="}}{{",{NS}}}'),
    ],
)
def test_every_selector_gains_the_namespace(query: str, confined: str) -> None:
    assert confine_to_namespace(query, "acme-web") == confined


@pytest.mark.parametrize(
    "query",
    [
        # Keywords Prometheus also reads as metric names where an operand belongs.
        "by",
        "sum(by)",
        "offset",
        "x + and",
        "without",
    ],
)
def test_a_keyword_read_as_a_metric_name_is_confined_too(query: str) -> None:
    confined = confine_to_namespace(query, "acme-web")

    assert NS in confined


def test_a_comment_cannot_hide_a_selector() -> None:
    assert confine_to_namespace("x # note {\n+ y", "acme-web") == f"x{{{NS}}} \n+ y{{{NS}}}"


@pytest.mark.parametrize(
    "query",
    [
        '"unterminated',
        'up{a="b"',
        "up{a=`b`",
        "x[foo{}]",
        'sum by ("a.b") (x)',
        "sum by x",
        "x; y",
        "x \\ y",
        "x" * 9000,
    ],
)
def test_an_expression_the_confiner_cannot_read_is_refused(query: str) -> None:
    with pytest.raises(PromqlScopeError):
        confine_to_namespace(query, "acme-web")


@pytest.mark.parametrize("namespace", ["", "Bad", 'x",y="z'])
def test_no_usable_namespace_refuses(namespace: str) -> None:
    with pytest.raises(PromqlScopeError):
        confine_to_namespace("up", namespace)


# ----- the resolver ----------------------------------------------------------


def _run(app, query, environment_name=None):
    with patch(
        "astrolift_observability.schema.queries.prometheus_client.query_range",
        return_value=[],
    ) as query_range:
        result = GoldenSignalsQuery().astrolift_execute_promql(
            _info(),
            app_slug=app.slug,
            query=query,
            start_unix=_START,
            end_unix=_END,
            step_seconds=_STEP,
            environment_name=environment_name,
        )
    return result, query_range


@pytest.mark.django_db
def test_the_resolver_runs_only_the_confined_query(permission_resolver):
    org, app = _scaffold()
    permission_resolver.grant(Permission.APP_READ)

    with _ctx(org):
        result, query_range = _run(app, "sum(rate(http_requests_total[5m]))")

    assert result.ok, result.error
    namespace = namespace_for_app(app)
    assert query_range.call_args.kwargs["query"] == (
        f'sum(rate(http_requests_total{{namespace="{namespace}"}}[5m]))'
    )


@pytest.mark.django_db
def test_the_resolver_confines_to_the_named_environments_namespace(permission_resolver):
    org, app = _scaffold()
    AppEnvironment.objects.filter(registered_app=app, name="prod").update(k8s_namespace="prom-exec-prod")
    permission_resolver.grant(Permission.APP_READ)

    with _ctx(org):
        result, query_range = _run(app, "up", environment_name="prod")

    assert result.ok, result.error
    assert query_range.call_args.kwargs["query"] == 'up{namespace="prom-exec-prod"}'


@pytest.mark.django_db
def test_the_resolver_refuses_what_it_cannot_confine_without_calling_prometheus(permission_resolver):
    org, app = _scaffold()
    permission_resolver.grant(Permission.APP_READ)

    with _ctx(org):
        result, query_range = _run(app, 'up{a="b"')

    assert result.ok is False
    assert "cannot be confined" in result.error
    query_range.assert_not_called()
