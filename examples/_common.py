"""Shared scaffolding for the example scripts under `examples/`.

Resolve the work_dir, optionally attach a Langfuse handler, stream a graph
node-by-node into a local result dict, print the wall-clock table and the
trace link. Not an example itself — hence the underscore prefix.
"""

from __future__ import annotations

import json as _json
import os
import sys
from pathlib import Path
from typing import Iterable

from pydantic import BaseModel as _BaseModel

from cmbagent_lg import default_work_dir, prepare_work_dir, save_trace_id
from cmbagent_lg.tracing import langfuse_host


# Bookkeeping fields suppressed from the streaming display. Per-example skip
# sets union with this.
STANDARD_SKIP_KEYS = frozenset({
    "attempts",
    "error_history",
    "work_dir",
    "node_elapsed_s",
    "step_number",
    "data_baseline",
    "data_manifest",
    "data_manifest_info",
    "step_feedback_history",
    "escalated",
})


# Reducer-managed lists that `stream_mode="updates"` yields as deltas: these
# must be concatenated, not overwritten, to mirror the graph's final state.
DEFAULT_LIST_REDUCED_KEYS = frozenset({"node_elapsed_s"})


def resolve_work_dir(argv: list[str]) -> Path:
    """`argv[1] > $WORK_DIR > default_work_dir()`, then `prepare_work_dir`.

    `prepare_work_dir` clears any existing contents (so reruns into the same
    path start clean). Set `KEEP_WORK_DIR=1` in the env to opt out.
    """
    if len(argv) > 1:
        wd = Path(argv[1])
    elif os.environ.get("WORK_DIR"):
        wd = Path(os.environ["WORK_DIR"])
    else:
        wd = default_work_dir()
    clear = not os.environ.get("KEEP_WORK_DIR")
    if clear and wd.exists():
        print(f"[work_dir] clearing existing       {wd}")
    wd = prepare_work_dir(wd, clear=clear)
    print(f"[work_dir] {wd.resolve()}")
    return wd


def trace_metadata(work_dir: Path, tags: list[str], run_name: str | None = None) -> dict:
    """Standard Langfuse metadata for an example run.

    Carries the full work_dir path alongside the basename session id, so a
    trace in the UI points back at the artifacts it produced on disk.
    """
    md = {
        "langfuse_session_id": Path(work_dir).name,
        "langfuse_tags": list(tags),
        "work_dir": str(Path(work_dir).resolve()),
    }
    if run_name:
        md["langfuse_trace_name"] = run_name
    return md


def attach_langfuse() -> tuple[object | None, list]:
    """Return `(handler, callbacks)` — `(None, [])` if Langfuse isn't configured."""
    try:
        from cmbagent_lg.tracing import langfuse_handler

        handler = langfuse_handler()
        print("[trace] langfuse handler attached")
        return handler, [handler]
    except Exception as e:  # noqa: BLE001 — Langfuse is optional everywhere
        print(f"[trace] skipping langfuse: {e}")
        return None, []


def render_value(v) -> str:
    """Pretty-print a state-delta value for the per-node display.

    Pydantic models render via their `.format()` if present (else JSON);
    JSON-looking strings are re-pretty-printed; everything else stringifies.
    """
    if isinstance(v, _BaseModel):
        fmt = getattr(v, "format", None)
        if callable(fmt):
            return fmt()
        return v.model_dump_json(indent=2)
    if isinstance(v, str):
        s = v.strip()
        if s and s[0] in "{[":
            try:
                return _json.dumps(_json.loads(s), indent=2)
            except Exception:
                pass
        return v
    try:
        return _json.dumps(v, indent=2, default=str)
    except Exception:
        return str(v)


def stream_and_render(
    graph,
    initial_state: dict,
    *,
    context,
    config: dict,
    skip_keys: Iterable[str] = (),
    list_reduced_keys: Iterable[str] = DEFAULT_LIST_REDUCED_KEYS,
) -> dict:
    """Stream the graph, printing each node's state delta and accumulating a
    `result` dict that mirrors the in-graph state — replaying `operator.add`
    reducers on `list_reduced_keys` so accumulated lists survive the loop.
    """
    skip = frozenset(STANDARD_SKIP_KEYS) | frozenset(skip_keys)
    reduced = frozenset(list_reduced_keys)

    result = dict(initial_state)
    for chunk in graph.stream(
        initial_state, context=context, config=config, stream_mode="updates"
    ):
        for node_name, delta in chunk.items():
            print(f"\n\n══════════ {node_name} ══════════")
            for k, v in (delta or {}).items():
                if k in skip:
                    continue
                print(f"\n── {k} ──")
                print(render_value(v))
            for k, v in (delta or {}).items():
                if k in reduced:
                    result[k] = list(result.get(k, [])) + list(v or [])
                else:
                    result[k] = v
    return result


def print_timings(timings: list[dict]) -> None:
    """The wall-clock-per-node table. Skipped if `timings` is empty."""
    if not timings:
        return
    print("\n=== TIMINGS (wall-clock) ===")
    width = max(len(t["node"]) for t in timings)
    for t in timings:
        print(f"  {t['node']:<{width}}  {t['elapsed_s']:7.2f}s")
    print(f"  {'TOTAL':<{width}}  {sum(t['elapsed_s'] for t in timings):7.2f}s")


def print_self_debug_verdicts(result: dict, ctx) -> None:
    """The "EXECUTION VERDICT / STEP VERDICT / attempts" footer that every
    standalone self_debug example prints."""
    exec_verdict = result.get("current_execution_verdict")
    step_verdict = result.get("current_step_verdict")
    print("\n\n=== EXECUTION VERDICT (did the code run cleanly?) ===")
    print(exec_verdict.format() if exec_verdict else "(none — exited before execution_evaluator)")
    print("\n=== STEP VERDICT (did it achieve the goal?) ===")
    print(step_verdict.format() if step_verdict else "(none — the code never ran cleanly)")
    extras = ""
    if result.get("escalated"):
        extras = " | escalated"
    print(
        f"\n=== attempts: {result.get('attempts', 0)} / {ctx.max_n_attempts}{extras} ==="
    )


def save_node_timings(work_dir: Path, step_number: int, timings: list[dict]) -> Path:
    """Persist a step's per-node wall-clock to `logs/step_{N}_timings.json`."""
    out_dir = Path(work_dir) / "logs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"step_{step_number}_timings.json"
    out.write_text(
        _json.dumps(
            {
                "node_elapsed_s": timings,
                "total_node_s": sum(t["elapsed_s"] for t in timings),
            },
            indent=2,
        )
    )
    return out


def print_trace_info(handler, work_dir: Path) -> None:
    """Save the Langfuse trace id (if any) and print the UI link + cost CLI hint."""
    if handler is not None and getattr(handler, "last_trace_id", None):
        trace_id = handler.last_trace_id
        tid_path = save_trace_id(trace_id, work_dir)
        print(f"\n[work_dir] langfuse trace id in    {tid_path}")
        print(f"[trace]    open in UI:             {langfuse_host()}/trace/{trace_id}")
        print(f"[trace]    cost summary:           cmbagent-lg-cost {work_dir}")
    else:
        print("\n[trace]    (no trace id — langfuse handler not attached)")
