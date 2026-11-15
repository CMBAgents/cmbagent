"""Wall-clock timing decorator for graph nodes.

Prints `[time] <name>: <x.xx>s` to stderr and merges the elapsed seconds into the
node's returned state delta as `node_elapsed_s`. Accumulation across repeated
node passes relies on the `Annotated[list[...], operator.add]` reducer declared
in each module's `state.py`.
"""

from __future__ import annotations

import sys
import time
from functools import wraps
from typing import Callable, TypedDict


class NodeTiming(TypedDict):
    node: str
    elapsed_s: float


def timed_node(name: str) -> Callable:
    """Decorator: wrap a langgraph node fn to measure + record wall-clock time."""

    def decorator(fn: Callable) -> Callable:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            t0 = time.perf_counter()
            result = fn(*args, **kwargs)
            elapsed = time.perf_counter() - t0
            print(f"[time] {name}: {elapsed:.2f}s", file=sys.stderr, flush=True)
            if isinstance(result, dict):
                result = {
                    **result,
                    "node_elapsed_s": [{"node": name, "elapsed_s": elapsed}],
                }
            return result

        return wrapper

    return decorator
