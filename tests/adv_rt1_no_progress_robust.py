"""RT1 / attack #2 — "adaptation with no progress" — DID NOT BREAK the loop.

Documented here so the mitigation phase does NOT over-fix.

Attack idea: reviewer always adapts; replanner returns the SAME remaining steps
(no growth, no shrink) — hoping for infinite re-planning "without advancing".

Why it is actually SAFE: `replan_tail` only ever rewrites plan[n_done:], i.e.
the NOT-yet-executed tail. The just-executed step lives in the fixed prefix
plan[:n_done]. `execute_step` (run_step) unconditionally does step_index += 1
every visit. So the pointer advances every round regardless of the review. If
the tail length is held constant, len(plan) stays constant while step_index
climbs, so `route_after_merge` (step_index > len(plan)) eventually fires and the
run ENDS normally.

=> Non-termination requires len(plan) to grow at least as fast as step_index
   (attack #1). Constant-length re-planning is self-limiting. Mitigations should
   target GROWTH / an absolute step cap, not "re-planning without advancing"
   (which cannot loop forever on its own).

Run:  python tests/adv_rt1_no_progress_robust.py
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cmbagent_lg.context import PlanContext
from cmbagent_lg.planning.schemas import Plan, Step
from cmbagent_lg.deep_research import schemas as adaptive_schemas
from cmbagent_lg.deep_research import nodes as adaptive_nodes
from cmbagent_lg.deep_research.graph import build_deep_research_graph


def _step(name: str) -> Step:
    return Step(sub_task=name, sub_task_agent="engineer", bullet_points=[f"do {name}"])


def test_constant_length_replanning_terminates():
    def _execute_step(state, runtime):
        n = state.get("step_index", 1)
        return {
            "step_summaries": state.get("step_summaries", []) + [f"summary {n}"],
            "step_outcomes": state.get("step_outcomes", []) + [{"step_number": n, "fulfilled": True}],
            "step_index": n + 1,
        }

    def _adaptive_review(state, runtime):
        return {"adaptive_review": adaptive_schemas.AdaptiveReview(
            needs_adaptation=True, recommendations=["reword, same steps"])}

    def _replan_tail(state, runtime):
        # Identical remaining steps — same length, no growth.
        n_done = state.get("step_index", 1) - 1
        remaining = list(state["plan"].sub_tasks[n_done:])
        return {"new_tail": Plan(sub_tasks=remaining)}

    saved = (adaptive_nodes.run_step, adaptive_nodes.adaptive_review, adaptive_nodes.replan_tail)
    adaptive_nodes.run_step = _execute_step
    adaptive_nodes.adaptive_review = _adaptive_review
    adaptive_nodes.replan_tail = _replan_tail
    try:
        compiled = build_deep_research_graph()
        ctx = PlanContext(main_task="no-progress")
        final = compiled.invoke(
            {"plan": Plan(sub_tasks=[_step("a"), _step("b"), _step("c")]), "work_dir": None},
            context=ctx,
            config={"recursion_limit": 80},
        )
    finally:
        (adaptive_nodes.run_step, adaptive_nodes.adaptive_review, adaptive_nodes.replan_tail) = saved

    ran = len(final["step_outcomes"])
    final_len = len(final["plan"].sub_tasks)
    print(f"constant-length re-planning: steps executed={ran}, final plan length={final_len}, "
          f"adaptations={len(final['adaptive_history'])} — TERMINATED normally")
    assert ran == 3 and final_len == 3
    print("ROBUST: constant-length re-planning is self-limiting (does NOT need a fix).")


if __name__ == "__main__":
    test_constant_length_replanning_terminates()
    print("adv_rt1_no_progress_robust: NO VULNERABILITY (documented as robust)")
