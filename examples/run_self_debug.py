"""Tiny end-to-end run of the engineer ↔ executor ↔ evaluator self-debug loop.

    python examples/run_self_debug.py                       # timestamped workdir
    python examples/run_self_debug.py runs/debug_primes     # explicit workdir
    WORK_DIR=runs/debug_primes python examples/run_self_debug.py

Requires GOOGLE_API_KEY in .env; Langfuse tracing is attached only if the
LANGFUSE_* keys are set. The `Step` is hardcoded — for the retry path see
`run_self_debug_retry.py`.
"""

import sys
from dotenv import load_dotenv

load_dotenv(override=True)

from cmbagent_lg import PlanContext, Step, self_debug_graph
from _common import (  # sibling module in examples/ (sys.path[0] = script dir)
    attach_langfuse,
    print_self_debug_verdicts,
    print_timings,
    print_trace_info,
    resolve_work_dir,
    save_node_timings,
    stream_and_render,
    trace_metadata,
)

# ── inputs ──────────────────────────────────────────────────────────────

ctx = PlanContext(
    main_task="Print the first 10 prime numbers, one per line, then print their sum.",
    hardware_constraints="Standard laptop. Single CPU. No GPU. 16 GB RAM.",
    code_execution_timeout=30,
    max_n_attempts=3,
)

step = Step(
    sub_task="Compute the first 10 prime numbers and print them, then their sum.",
    sub_task_agent="engineer",
    bullet_points=[
        "Use a simple trial-division primality test.",
        "Print each prime on its own line.",
        "After the list, print 'sum=<value>'.",
    ],
    code_execution_timeout=30,
)

STEP_NUMBER = 1  # standalone run — deep_research passes the real plan index

# ── run ─────────────────────────────────────────────────────────────────

work_dir = resolve_work_dir(sys.argv)
handler, callbacks = attach_langfuse()

task_snippet = ctx.main_task.strip().split("\n")[0][:60]
run_name = f"self_debug · {task_snippet} · {work_dir.name}"
tags = ["self_debug", work_dir.name]

result = stream_and_render(
    self_debug_graph,
    {"step": step, "work_dir": str(work_dir), "step_number": STEP_NUMBER},
    context=ctx,
    config={
        "callbacks": callbacks,
        "run_name": run_name,
        "tags": tags,
        "metadata": trace_metadata(work_dir, tags, run_name),
    },
)

# ── summary ─────────────────────────────────────────────────────────────

print_self_debug_verdicts(result, ctx)
timings = result.get("node_elapsed_s", [])
print_timings(timings)
save_node_timings(work_dir, STEP_NUMBER, timings)
print(f"\n[work_dir] code under               {work_dir}/codebase/")
print(f"[work_dir] verdict + timings under  {work_dir}/logs/")
print_trace_info(handler, work_dir)
