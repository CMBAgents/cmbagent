"""Self-debug example that should require a retry.

The step demands `scipy.signal.gaussian`, moved to
`scipy.signal.windows.gaussian` in scipy 1.13, so attempt 1 fails with an
ImportError and the loop has to find the new path. Prints the error history.

    python examples/run_self_debug_retry.py runs/debug_retry
"""

import sys
from dotenv import load_dotenv

load_dotenv(override=True)

from cmbagent_lg import PlanContext, Step, self_debug_graph
from _common import (
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
    main_task=(
        "Smooth a noisy 1D signal with a Gaussian window from scipy and print "
        "the first few smoothed values."
    ),
    hardware_constraints="Standard laptop. Single CPU. No GPU. 16 GB RAM.",
    code_execution_timeout=30,
    max_n_attempts=3,
)

step = Step(
    sub_task="Smooth a noisy 1D signal using a Gaussian window from scipy.",
    sub_task_agent="engineer",
    bullet_points=[
        "Generate a 1000-sample noisy sine: y = sin(2*pi*5*t/1000) + Gaussian noise (std=0.3, seed=0).",
        "Build the smoothing kernel by importing `gaussian` from `scipy.signal` (i.e. "
        "`from scipy.signal import gaussian; window = gaussian(M=51, std=7)`).",
        "Do NOT use `scipy.signal.windows.gaussian` or `scipy.signal.windows.*` — the "
        "downstream verification script does an AST check that the import is exactly "
        "`from scipy.signal import gaussian`. Any other path fails verification.",
        "Normalize the kernel so it sums to 1.",
        "Convolve with the signal using numpy.convolve(mode='same').",
        "Print the first 5 values of the smoothed signal, one per line.",
    ],
    code_execution_timeout=30,
)

STEP_NUMBER = 1

# ── run ─────────────────────────────────────────────────────────────────

work_dir = resolve_work_dir(sys.argv)
handler, callbacks = attach_langfuse()

task_snippet = ctx.main_task.strip().split("\n")[0][:60]
run_name = f"self_debug · {task_snippet} · {work_dir.name}"
tags = ["self_debug", "retry_test", work_dir.name]

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

err_hist = result.get("error_history", [])
if err_hist:
    print("\n=== ERROR HISTORY ===")
    for i, e in enumerate(err_hist, start=1):
        print(f"  attempt {i}: {e[:200]}{'…' if len(e) > 200 else ''}")

timings = result.get("node_elapsed_s", [])
print_timings(timings)
save_node_timings(work_dir, STEP_NUMBER, timings)
print(f"\n[work_dir] code under               {work_dir}/codebase/")
print(f"[work_dir] verdict + timings under  {work_dir}/logs/")
print_trace_info(handler, work_dir)
