"""Self-debug example that triggers the escalation escape hatch.

The Step pins `numpy.trapz`, which NumPy 2.0 renamed to `np.trapezoid` and
then removed, so the run fails with a bare `AttributeError`. With
`enable_escalation=True` the failure is handed once to a Claude Agent SDK
agent, which edits `codebase/step_1.py` and returns control to the executor.

Requires ANTHROPIC_API_KEY in .env and `pip install claude-agent-sdk`.

    python examples/run_self_debug_escalation.py runs/escalation
"""

import json
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
    main_task="Numerically integrate a function with the trapezoidal rule.",
    hardware_constraints="Standard laptop. Single CPU. No GPU. 16 GB RAM.",
    code_execution_timeout=30,
    max_n_attempts=3,
    # The escape hatch — opt-in. Needs ANTHROPIC_API_KEY + claude-agent-sdk.
    enable_escalation=True,
    escalation_max_budget_usd=0.50,
    escalation_max_turns=12,
    escalation_model="claude-haiku-4-5-20251001",
)

step = Step(
    sub_task="Numerically integrate sin(x) on [0, pi] with the trapezoidal rule.",
    sub_task_agent="engineer",
    bullet_points=[
        "Build an array of 1000 evenly spaced x values from 0 to pi.",
        "Evaluate y = sin(x) on that grid.",
        # Pinned to the removed API on purpose — this is what forces the
        # `renamed_api` failure that triggers escalation.
        "Integrate y over x by calling numpy.trapz(y, x) — use that exact "
        "function. Do NOT substitute scipy, np.trapezoid, or a manual "
        "implementation; numpy.trapz is a hard requirement.",
        "Print the integral value (the exact answer is 2.0).",
    ],
    code_execution_timeout=30,
)

STEP_NUMBER = 1

# ── run ─────────────────────────────────────────────────────────────────

work_dir = resolve_work_dir(sys.argv)
handler, callbacks = attach_langfuse()

task_snippet = ctx.main_task.strip().split("\n")[0][:60]
run_name = f"self_debug · {task_snippet} · {work_dir.name}"
tags = ["self_debug", "escalation", work_dir.name]

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

esc_path = work_dir / "logs" / f"step_{STEP_NUMBER}_escalation.json"
if esc_path.is_file():
    rec = json.loads(esc_path.read_text())
    print("\n=== ESCALATION ===")
    print(f"  reason:       {rec['reason']}")
    print(f"  cost:         ${rec['cost_usd']:.4f}")
    print(f"  turns:        {rec['turns']}")
    print(f"  tool calls:   {rec['tool_calls']}")
    print(f"  code changed: {rec['code_changed']}")

print(f"\n[work_dir] code under               {work_dir}/codebase/")
print(f"[work_dir] verdict + logs under     {work_dir}/logs/")
print_trace_info(handler, work_dir)
