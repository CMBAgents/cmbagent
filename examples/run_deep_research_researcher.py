"""End-to-end deep_research run with a researcher → engineer hand-off.

`deep_research_graph` routes the researcher step through `researcher_graph`
(single `step_evaluator` gate) and the engineer step through
`self_debug_graph` (the two-gate loop). The engineer sees the researcher's
markdown report in `previous_steps_execution_summary`.

    python examples/run_deep_research_researcher.py runs/dr_mixed
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
        "Compare classical 1D smoothing approaches and implement the most "
        "appropriate one on a synthetic noisy signal, producing a diagnostic "
        "plot of raw vs smoothed."
    ),
    hardware_constraints="Standard laptop. Single CPU. No GPU. 16 GB RAM.",
    code_execution_timeout=60,
    max_n_attempts=3,
    available_agents=[
        ("researcher", "Reads, reasons, writes prose; no code execution."),
        ("engineer", "Writes and runs Python code: numeric work, data I/O, plotting."),
    ],
    maximum_number_of_steps_in_plan=2,
    num_rounds=1,
    # Pin the mixed shape: left free, the planner tends to merge everything
    # into a single engineer step.
    planner_append_instructions=(
        "Produce exactly 2 steps: step 1 must be a `researcher` step that "
        "compares smoothing methods (e.g. moving average, Savitzky-Golay, "
        "Gaussian kernel) and recommends ONE. Step 2 must be an `engineer` "
        "step that implements the recommended method on a synthetic noisy "
        "signal it generates itself, saves a diagnostic plot, and prints "
        "the saved path."
    ),
)

# ── workdir + tracing ───────────────────────────────────────────────────

work_dir = resolve_work_dir(sys.argv)
handler, callbacks = attach_langfuse()

task_snippet = ctx.main_task.strip().split("\n")[0][:60]
run_name = f"deep_research+researcher · {task_snippet} · {work_dir.name}"
tags = ["deep_research", "mixed", work_dir.name]  # not agent names: per-node tags drive cost attribution
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
    if o.get("escalated"):
        extras.append("escalated")
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
