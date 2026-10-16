"""Confine a caller's PromQL to the series of one namespace (#2418).

``astroliftExecutePromql`` used to pass the expression to the shared cluster
Prometheus verbatim, so ``APP_READ`` on any app read every tenant's metrics.
:func:`confine_to_namespace` adds a ``namespace="<ns>"`` matcher to every
vector selector in the expression, the way prom-label-proxy does. Prometheus
ANDs the matchers on one label, so a selector of the caller's own naming
another namespace selects nothing rather than that namespace.

Selectors are PromQL's only data source, so an expression whose every
selector is confined reads nothing outside the namespace. The scanner is
deliberately narrower than PromQL: anything it does not recognise is refused
rather than passed through, so Prometheus never reads a selector this module
did not see.
"""

from __future__ import annotations

import re

#: Longer than any expression an operator types into the panel.
MAX_QUERY_LENGTH = 8192

_IDENT = re.compile(r"[A-Za-z_:][A-Za-z0-9_:]*")
#: Numbers, durations (``5m``, ``1h30m``), hex and exponent literals.
_NUMBER = re.compile(r"(?:0[xX][0-9a-fA-F]+|[0-9]*\.?[0-9]+(?:[eE][+-]?[0-9]+)?(?:[a-zA-Z]+[0-9]*)*)")
#: Range and subquery brackets: ``[5m]``, ``[1h:30s]``.
_RANGE = re.compile(r"\[[0-9A-Za-z_:. \t]*\]")
_OPERATORS = ("==", "!=", ">=", "<=", "=~", "!~", "+", "-", "*", "/", "%", "^", "<", ">", ",", "(", "@")
_AGGREGATIONS = frozenset(
    {
        "sum",
        "avg",
        "count",
        "min",
        "max",
        "group",
        "stddev",
        "stdvar",
        "topk",
        "bottomk",
        "count_values",
        "quantile",
        "limitk",
        "limit_ratio",
    },
)
#: Keywords that Prometheus also accepts as a metric name where an operand is
#: expected. There they are a selector and are confined like any other.
_OPERAND_KEYWORDS = frozenset({"by", "without", "and", "or", "unless", "offset"})
#: Keywords that are never a metric name. The grouping ones may take a
#: parenthesised list of label names, which are not selectors.
_MODIFIERS = frozenset({"on", "ignoring", "group_left", "group_right", "bool", "atan2"})
_LABEL_LIST = re.compile(r"\(\s*(?:[A-Za-z_][A-Za-z0-9_]*\s*(?:,\s*[A-Za-z_][A-Za-z0-9_]*\s*)*,?\s*)?\)")


class PromqlScopeError(ValueError):
    """The expression cannot be confined, so it is not run."""


def _string_end(query: str, start: int) -> int:
    """The index just past the string literal opening at ``start``."""
    quote = query[start]
    position = start + 1
    while position < len(query):
        char = query[position]
        if char == "\\" and quote != "`":
            position += 2
            continue
        if char == quote:
            return position + 1
        if char == "\n" and quote != "`":
            break
        position += 1
    raise PromqlScopeError("unterminated string literal")


def _skip_space(query: str, position: int) -> int:
    while position < len(query):
        if query[position].isspace():
            position += 1
        elif query[position] == "#":
            newline = query.find("\n", position)
            position = len(query) if newline < 0 else newline
        else:
            break
    return position


def _matchers_end(query: str, start: int) -> int:
    """The index of the ``}`` closing the matcher block opening at ``start``."""
    position = start + 1
    while position < len(query):
        char = query[position]
        if char in "\"'`":
            position = _string_end(query, position)
        elif char == "}":
            return position
        elif char in "{#":
            break
        else:
            position += 1
    raise PromqlScopeError("unterminated label matcher block")


def _with_matcher(block: str, matcher: str) -> str:
    """``block`` (the text between the braces) with ``matcher`` added."""
    inner = block.strip()
    if inner and not inner.endswith(","):
        inner += ","
    return "{" + inner + matcher + "}"


def confine_to_namespace(query: str, namespace: str) -> str:
    """``query`` with every vector selector restricted to ``namespace``.

    Raises :class:`PromqlScopeError` for an expression the scanner cannot
    confine with certainty. Comments are dropped.
    """
    if not namespace or not re.fullmatch(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?", namespace):
        raise PromqlScopeError("no namespace to confine the query to")
    if len(query) > MAX_QUERY_LENGTH:
        raise PromqlScopeError(f"query is longer than {MAX_QUERY_LENGTH} characters")
    matcher = f'namespace="{namespace}"'
    out: list[str] = []
    # Whether the next token is an operand (a selector, literal or call) or
    # follows one (a binary operator, modifier or closing bracket).
    operand = True
    position = 0

    def selector(word: str, after: int) -> int:
        following = _skip_space(query, after)
        if query[following : following + 1] == "{":
            close = _matchers_end(query, following)
            out.append(word + _with_matcher(query[following + 1 : close], matcher))
            return close + 1
        out.append(word + "{" + matcher + "}")
        return after

    def label_list(word: str, after: int) -> int:
        following = _skip_space(query, after)
        labels = _LABEL_LIST.match(query, following)
        if labels is None:
            raise PromqlScopeError(f"{word!r} needs a list of plain label names")
        out.append(query[after : labels.end()])
        return labels.end()

    while position < len(query):
        char = query[position]
        if char.isspace():
            out.append(char)
            position += 1
        elif char == "#":
            newline = query.find("\n", position)
            position = len(query) if newline < 0 else newline
        elif char in "\"'`":
            end = _string_end(query, position)
            out.append(query[position:end])
            position, operand = end, False
        elif char == "{":
            end = _matchers_end(query, position)
            out.append(_with_matcher(query[position + 1 : end], matcher))
            position, operand = end + 1, False
        elif char == "[":
            match = _RANGE.match(query, position)
            if match is None:
                raise PromqlScopeError("unsupported range or subquery")
            out.append(match.group())
            position, operand = match.end(), False
        elif char == ")":
            out.append(char)
            position, operand = position + 1, False
        elif char.isdigit() or (char == "." and query[position + 1 : position + 2].isdigit()):
            match = _NUMBER.match(query, position)
            if match is None:
                raise PromqlScopeError("unsupported number literal")
            out.append(match.group())
            position, operand = match.end(), False
        elif (ident := _IDENT.match(query, position)) is not None:
            word = ident.group()
            end = ident.end()
            lowered = word.lower()
            following = _skip_space(query, end)
            next_char = query[following : following + 1]
            if lowered in ("inf", "nan"):
                out.append(word)
                position, operand = end, False
            elif not operand and lowered in ("by", "without"):
                out.append(word)
                position, operand = label_list(word, end), False
            elif not operand and lowered in ("and", "or", "unless", "offset"):
                out.append(word)
                position, operand = end, True
            elif lowered in _MODIFIERS:
                out.append(word)
                position, operand = end, True
                if lowered in ("on", "ignoring", "group_left", "group_right") and next_char == "(":
                    position = label_list(word, end)
            elif operand and lowered in _OPERAND_KEYWORDS:
                # Prometheus reads a keyword where an operand belongs as a metric name.
                position, operand = selector(word, end), False
            elif next_char == "(":
                # A function or aggregation call.
                out.append(word)
                position = end
            elif (
                lowered in _AGGREGATIONS
                and _IDENT.match(query, following) is not None
                and (
                    _IDENT.match(query, following).group().lower() in ("by", "without")  # type: ignore[union-attr]
                )
            ):
                # ``sum by (label) (expr)``: the grouping comes first.
                out.append(word)
                position, operand = end, False
            else:
                position, operand = selector(word, end), False
        else:
            for operator in _OPERATORS:
                if query.startswith(operator, position):
                    out.append(operator)
                    position += len(operator)
                    operand = True
                    break
            else:
                raise PromqlScopeError(f"unsupported character {char!r}")
    return "".join(out)
