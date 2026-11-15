"""Tiny end-to-end run of the planner ↔ plan_reviewer loop.

    python examples/run_planner_review.py                       # timestamped workdir
    python examples/run_planner_review.py runs/cmb_lensing_v1   # explicit workdir
    WORK_DIR=runs/cmb_lensing_v1 python examples/run_planner_review.py

Requires GOOGLE_API_KEY in .env. Langfuse tracing attached if LANGFUSE_*
keys are set; otherwise the run proceeds without tracing.
"""

import sys
from dotenv import load_dotenv

load_dotenv(override=True)

from cmbagent_lg import graph, PlanContext, save_final_plan
from _common import (
    attach_langfuse,
    print_timings,
    print_trace_info,
    resolve_work_dir,
    stream_and_render,
    trace_metadata,
)

# ── inputs ──────────────────────────────────────────────────────────────

ctx = PlanContext(
    main_task=(
        "Reconstruct the CMB lensing potential from a single Planck-like temperature "
        "map. We have only the map; no external catalogs."
    ),
    hardware_constraints="Standard laptop. Single CPU. No GPU. 16 GB RAM.",
    code_execution_timeout=120,
    maximum_number_of_steps_in_plan=5,
    num_rounds=2,  # 2 review cycles → plan v1 → review → v2 → review → v3 (final)
    available_agents=[
        ("engineer", "Writes and runs Python code: numeric work, data I/O, computations, plotting."),
        ("researcher", "Reads, reasons, writes prose; no code execution."),
    ],
)

# ── run ─────────────────────────────────────────────────────────────────

work_dir = resolve_work_dir(sys.argv)
handler, callbacks = attach_langfuse()

task_snippet = ctx.main_task.strip().split("\n")[0][:60]
run_name = f"planning · {task_snippet} · {work_dir.name}"
tags = ["planning", work_dir.name]

result = stream_and_render(
    graph,
    {},
    context=ctx,
    config={
        "callbacks": callbacks,
        "run_name": run_name,
        "tags": tags,
        "metadata": trace_metadata(work_dir, tags, run_name),
    },
    skip_keys={"round", "history"},
)

# ── summary ─────────────────────────────────────────────────────────────

print("\n\n=== FINAL PLAN ===")
print(result["current_plan"].format())
print(f"\n=== HISTORY: {len(result.get('history', []))} reviewed round(s) before the final plan ===")

print_timings(result.get("node_elapsed_s", []))

plan_path = save_final_plan(result["current_plan"], work_dir)
print(f"\n[work_dir] final plan saved to     {plan_path}")
print_trace_info(handler, work_dir)
