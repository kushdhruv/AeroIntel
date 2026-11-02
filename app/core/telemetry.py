"""OpenTelemetry seams; exporter configuration belongs to the host application."""

from collections.abc import Callable
from functools import wraps
from typing import ParamSpec, TypeVar

from opentelemetry import trace

P = ParamSpec("P")
R = TypeVar("R")


def traced(name: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    def decorate(function: Callable[P, R]) -> Callable[P, R]:
        @wraps(function)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            # Deliberately exclude image contents, tokens and arbitrary tool arguments.
            with trace.get_tracer("aerointel").start_as_current_span(name, record_exception=False):
                return function(*args, **kwargs)

        return wrapped

    return decorate
