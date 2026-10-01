"""Bounded Standard ARM observations and non-polling SDK acceptance.

Request admission and transport phases are bounded. These limits do not cancel
an external operation or guarantee an absolute deadline for streamed bodies.
"""

from __future__ import annotations

import json
import time
from contextvars import ContextVar
from functools import wraps
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlsplit

from azure._event_grid_namespace_ownership import OwnershipUnknown
from azure.core.polling import NoPolling

if TYPE_CHECKING:
    from collections.abc import Callable

_DEADLINE: ContextVar[float | None] = ContextVar("eventgrid_standard_deadline", default=None)


def bounded[**P, R](function: Callable[P, R]) -> Callable[P, R]:
    @wraps(function)
    def operation(*args: P.args, **kwargs: P.kwargs) -> R:
        proposed = time.monotonic() + 20
        previous = _DEADLINE.get()
        token = _DEADLINE.set(min(previous, proposed) if previous is not None else proposed)
        try:
            return function(*args, **kwargs)
        finally:
            _DEADLINE.reset(token)

    return operation


class Observation:
    def call(self, function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        deadline = _DEADLINE.get()
        remaining = deadline - time.monotonic() if deadline is not None else 0
        if remaining <= 0:
            raise OwnershipUnknown("bounded Standard observation budget is exhausted")
        return function(
            *args,
            connection_timeout=min(5, remaining),
            read_timeout=min(5, remaining),
            retry_total=0,
            redirect_max=0,
            **kwargs,
        )

    def begin(self, function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        poller = self.call(function, *args, polling=False, **kwargs)
        polling_method = getattr(poller, "polling_method", None)
        if not callable(polling_method) or not isinstance(polling_method(), NoPolling):
            raise OwnershipUnknown("SDK non-polling acceptance could not be established")
        return poller.result(timeout=0)

    def pages(self, function: Callable[..., Any], collection: str, *args: Any, **kwargs: Any) -> list[Any]:
        page_count = 0

        def checked_response(response: Any) -> None:
            nonlocal page_count
            page_count += 1
            raw = response.http_response.body()
            if len(raw) > 2 * 1024 * 1024:
                raise OwnershipUnknown("inventory response exceeds the observation limit")
            try:
                value = json.loads(raw)
            except (ValueError, TypeError, RecursionError) as exc:
                raise OwnershipUnknown("inventory response is malformed") from exc
            if not isinstance(value, dict) or not isinstance(value.get("value"), list):
                raise OwnershipUnknown("complete inventory is not observed")
            following = value.get("nextLink")
            if following:
                if page_count >= 4 or not isinstance(following, str):
                    raise OwnershipUnknown("inventory exceeds the four-page observation limit")
                try:
                    parsed = urlsplit(following)
                    trusted = (
                        parsed.scheme == "https"
                        and parsed.hostname == "management.azure.com"
                        and parsed.port in {None, 443}
                        and not parsed.username
                        and not parsed.password
                        and not parsed.fragment
                        and ("/" + unquote(parsed.path).lstrip("/")).casefold() == collection.casefold()
                    )
                except ValueError:
                    trusted = False
                if not trusted:
                    raise OwnershipUnknown("inventory continuation leaves the exact trusted collection")

        pager = self.call(function, *args, raw_response_hook=checked_response, **kwargs)
        if not callable(getattr(pager, "by_page", None)):
            raise OwnershipUnknown("complete paged SDK inventory is unavailable")
        result: list[Any] = []
        pages = pager.by_page()
        visited = 0
        while True:
            deadline = _DEADLINE.get()
            if deadline is None or time.monotonic() >= deadline:
                raise OwnershipUnknown("inventory observation budget is exhausted")
            try:
                page = next(pages)
            except StopIteration:
                return result
            visited += 1
            if visited > 4:
                raise OwnershipUnknown("inventory exceeds the four-page observation limit")
            if page_count != visited:
                raise OwnershipUnknown("inventory page transport proof is unavailable")
            for item in page:
                if len(result) >= 128:
                    raise OwnershipUnknown("inventory exceeds the 128-item observation limit")
                if time.monotonic() >= deadline:
                    raise OwnershipUnknown("inventory observation budget is exhausted")
                result.append(item)
