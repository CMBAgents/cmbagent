"""Self-debug example that produces a plot — the Lorenz attractor.

Output files go in `{work_dir}/data/`: the subprocess runs with cwd = work_dir,
so the generated script writes there via the relative path `data/...`.

    python examples/run_self_debug_lorenz.py runs/lorenz
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
    main_task="Integrate the Lorenz system and plot its attractor.",
    hardware_constraints="Standard laptop. Single CPU. No GPU. 16 GB RAM.",
    code_execution_timeout=60,
    max_n_attempts=3,
)

step = Step(
    sub_task="Plot the Lorenz attractor.",
    sub_task_agent="engineer",
    bullet_points=[
        "Integrate the Lorenz system (sigma=10, rho=28, beta=8/3) from t=0 to "
        "t=40 with a fine time step, starting from the point (0.0, 1.0, 1.05).",
        "Use scipy.integrate.solve_ivp for the integration.",
        "Make a 3D line plot of the trajectory (x, y, z) with matplotlib.",
        "Save the figure to data/lorenz_attractor.png at dpi>=300 and print the "
        "saved path.",
    ],
    code_execution_timeout=60,
)

STEP_NUMBER = 1

# ── run ─────────────────────────────────────────────────────────────────

work_dir = resolve_work_dir(sys.argv)
handler, callbacks = attach_langfuse()

task_snippet = ctx.main_task.strip().split("\n")[0][:60]
run_name = f"self_debug · {task_snippet} · {work_dir.name}"
tags = ["self_debug", "lorenz", work_dir.name]

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

data_dir = work_dir / "data"
produced = sorted(p.name for p in data_dir.iterdir()) if data_dir.is_dir() else []
print(f"\n[work_dir] code under               {work_dir}/codebase/")
print(f"[work_dir] data files               {produced or '(none)'}")
print(f"[work_dir] verdict + timings under  {work_dir}/logs/")
print_trace_info(handler, work_dir)
