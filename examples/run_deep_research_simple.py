"""Minimal end-to-end demo: planning → deep_research.

Two-step toy task on numpy + matplotlib only, escalation off: generate a
dataset, then load it back and plot it. The point is the cross-step carryover
— step 2's prompt is threaded with step 1's code, stdout and file manifest, so
it can reference the file path step 1 chose.

    python examples/run_deep_research_simple.py runs/deep_research_simple
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(override=True)

from cmbagent_lg import (
    PlanContext,
    graph as planning_graph,
    deep_research_graph,
    save_final_plan,
    save_deep_research_summary,
)
from _common import (
    attach_langfuse,
    print_timings,
    print_trace_info,
    resolve_work_dir,
    trace_metadata,
)

# ── inputs ──────────────────────────────────────────────────────────────

ctx = PlanContext(
    main_task=(
        "Generate a noisy 1D sinusoidal dataset and save it to disk, "
        "then load it back and produce a diagnostic scatter plot with "
        "the underlying clean curve overlaid."
    ),
    hardware_constraints="Standard laptop. Single CPU. No GPU.",
    code_execution_timeout=60,
    max_n_attempts=2,
    available_agents=[
        ("engineer", "Writes and runs Python code: numeric work, data I/O, plotting. Use only numpy and matplotlib."),
    ],
    maximum_number_of_steps_in_plan=2,
    num_rounds=1,
    enable_escalation=False,
)

# ── workdir + tracing ───────────────────────────────────────────────────

work_dir = resolve_work_dir(sys.argv)
handler, callbacks = attach_langfuse()

task_snippet = ctx.main_task.strip().split("\n")[0][:60]
run_name = f"deep_research_simple · {task_snippet} · {work_dir.name}"
tags = ["deep_research_simple", work_dir.name]
common_config = {
    "callbacks": callbacks,
    "run_name": run_name,
    "tags": tags,
    "metadata": trace_metadata(work_dir, tags, run_name),
}

# ── 1. plan ─────────────────────────────────────────────────────────────

print("\n══════════ PLANNING ══════════")
plan_result = planning_graph.invoke({}, context=ctx, config=common_config)
plan = plan_result["current_plan"]
save_final_plan(plan, work_dir)
print(plan.format())

# ── 2. execute ──────────────────────────────────────────────────────────

print("\n══════════ DEEP_RESEARCH ══════════")
dr_result = deep_research_graph.invoke(
    {"plan": plan, "work_dir": str(work_dir)},
    context=ctx,
    config=common_config,
)

# ── summary ─────────────────────────────────────────────────────────────

outcomes = dr_result.get("step_outcomes", [])
all_ok = bool(outcomes and all(o.get("fulfilled") for o in outcomes))

print("\n\n=== STEP OUTCOMES ===")
for o in outcomes:
    flag = "✓" if o.get("fulfilled") else "✗"
    extras = []
    if "attempts" in o:
        extras.append(f"attempts={o['attempts']}")
    if not o.get("fulfilled") and o.get("reason"):
        extras.append(f"reason={o['reason']!r}")
    print(f"  {flag} step {o['step_number']}  " + " ".join(extras))

print(
    f"\n=== PLAN: {'COMPLETE' if all_ok else 'HALTED'} "
    f"({len(outcomes)}/{len(plan.sub_tasks)} steps executed) ==="
)

print_timings(dr_result.get("node_elapsed_s", []))

save_deep_research_summary(
    work_dir, plan, outcomes, dr_result.get("step_summaries", [])
)

print("\n=== WORK_DIR TREE ===")
for root, _, files in sorted(os.walk(work_dir)):
    rel = Path(root).relative_to(work_dir)
    for f in sorted(files):
        print(f"  {rel / f if str(rel) != '.' else f}")

print_trace_info(handler, work_dir)
