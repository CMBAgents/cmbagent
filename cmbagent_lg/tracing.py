"""Langfuse tracing.

The key pair (LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY) picks the project. The server is
Langfuse Cloud by default, or a self-hosted instance: set LANGFUSE_BASE_URL (the SDK reads
it) and LANGFUSE_HOST (other tooling reads it) to the same value. See README.md § Langfuse.

    graph.invoke({}, context=ctx, config={"callbacks": [langfuse_handler()]})

LangChain callbacks propagate into child runs, so passing the handler once traces
every LLM call in the graph — including `with_structured_output` formatter calls.
"""

import logging
import os
from functools import lru_cache


LANGFUSE_DEFAULT_HOST = "https://cloud.langfuse.com"


def langfuse_host() -> str:
    """LANGFUSE_BASE_URL (the var the SDK reads), else LANGFUSE_HOST, else Langfuse Cloud."""
    return (
        os.environ.get("LANGFUSE_BASE_URL")
        or os.environ.get("LANGFUSE_HOST")
        or LANGFUSE_DEFAULT_HOST
    ).rstrip("/")


@lru_cache(maxsize=1)
def langfuse_handler():
    """Build a `langfuse.langchain.CallbackHandler` from LANGFUSE_* env vars.

    Cached per process so all `graph.invoke` calls share one handler (and one
    trace context). Requires langfuse>=4 and the umbrella `langchain` package.
    """
    public = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret = os.environ.get("LANGFUSE_SECRET_KEY")
    if not public or not secret:
        raise RuntimeError(
            "LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY not set. Put the keys of your Langfuse "
            "project into .env, with LANGFUSE_BASE_URL set to its server (Langfuse Cloud or a "
            "self-hosted instance)."
        )

    # Without BASE_URL the SDK would use Langfuse cloud, which 401s self-hosted keys.
    os.environ["LANGFUSE_BASE_URL"] = langfuse_host()

    from langfuse.langchain import CallbackHandler

    return CallbackHandler()  # creds + host from the env vars above


def langfuse_callbacks() -> list:
    """Safe ``callbacks`` list for a graph/LLM ``config`` — ``[handler]`` when
    LANGFUSE_* is configured, else ``[]``.

    Unlike :func:`langfuse_handler` (which raises, to fail loud in local dev),
    this never raises, so it can be spliced into every ``graph.invoke`` without
    env-guards at the call sites.
    """
    # Say WHY tracing is off: a silent [] makes a misconfiguration
    # indistinguishable from a working setup. A log line, not an exception — the
    # no-op guarantee is unchanged.
    if not os.environ.get("LANGFUSE_PUBLIC_KEY") or not os.environ.get("LANGFUSE_SECRET_KEY"):
        logging.getLogger(__name__).warning(
            "langfuse tracing DISABLED: %s not set (base_url=%s)",
            " and ".join(k for k in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")
                         if not os.environ.get(k)),
            os.environ.get("LANGFUSE_BASE_URL") or os.environ.get("LANGFUSE_HOST") or "UNSET")
        return []
    try:
        return [langfuse_handler()]
    except Exception as e:
        # Most likely a missing langfuse import or a bad host; the run continues
        # untraced, but says so.
        logging.getLogger(__name__).warning("langfuse tracing DISABLED: handler build failed: %r", e)
        return []
