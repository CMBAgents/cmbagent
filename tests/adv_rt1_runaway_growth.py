"""RT1 / TERMINATION attack #1 — RUNAWAY PLAN GROWTH (now MITIGATED).

Original vuln: the adaptive reviewer always returns needs_adaptation=True and the
replanner always returns a tail ONE STEP LONGER than what remains, so
`merge_plan` grew the plan by 1 every round while `step_index` also grew by 1 —
the gap (len - step_index) never shrank, `route_after_merge` never fired, and the
loop only stopped by crashing on LangGraph's recursion_limit.

Mitigation (feature R-02, two independent backstops):
  1. `merge_plan` caps the merged length at `ctx.maximum_number_of_steps_in_plan`
     by truncating the TAIL (never the frozen prefix).
  2. `route_after_review` stops routing to `replan_tail` once
     `len(adaptive_history)` reaches `ctx.max_adaptations`.

This test now asserts the FIXED behavior: the same adversarial always-grow driver
TERMINATES cleanly (no GraphRecursionError), the plan length never exceeds the
cap, and no more than `max_adaptations` adaptations are applied.

Run:  python tests/adv_rt1_runaway_growth.py
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from langgraph.errors import GraphRecursionError

from cmbagent_lg.context import PlanContext
from cmbagent_lg.planning.schemas import Plan, Step
from cmbagent_lg.deep_research import schemas as adaptive_schemas
from cmbagent_lg.deep_research import nodes as adaptive_nodes
from cmbagent_lg.deep_research.graph import build_deep_research_graph


def _step(name: str) -> Step:
    return Step(sub_task=name, sub_task_agent="engineer", bullet_points=[f"do {name}"])


def test_runaway_growth_is_bounded_and_terminates():
    # Records len(plan) seen at the start of each executed step.
    plan_lengths: list[int] = []

    def _execute_step(state, runtime):
        n = state.get("step_index", 1)
        plan_lengths.append(len(state["plan"].sub_tasks))
        return {
            "step_summaries": state.get("step_summaries", []) + [f"summary {n}"],
            "step_outcomes": state.get("step_outcomes", []) + [{"step_number": n, "fulfilled": True}],
            "step_index": n + 1,
        }

    def _adaptive_review(state, runtime):
        # ALWAYS adapt (with a non-empty, actionable recommendation).
        return {"adaptive_review": adaptive_schemas.AdaptiveReview(
            needs_adaptation=True, recommendations=["add a step"], reason="adversarial")}

    def _replan_tail(state, runtime):
        # ALWAYS return a tail ONE step longer than what remains.
        n_done = state.get("step_index", 1) - 1
        remaining = state["plan"].sub_tasks[n_done:]
        longer = [_step(f"t{i}") for i in range(len(remaining) + 1)]
        return {"new_tail": Plan(sub_tasks=longer)}

    saved = (adaptive_nodes.run_step, adaptive_nodes.adaptive_review, adaptive_nodes.replan_tail)
    adaptive_nodes.run_step = _execute_step
    adaptive_nodes.adaptive_review = _adaptive_review
    adaptive_nodes.replan_tail = _replan_tail
    try:
        compiled = build_deep_research_graph()
        ctx = PlanContext(main_task="runaway", maximum_number_of_steps_in_plan=5,
                          max_adaptations=3)
        crashed = False
        try:
            final = compiled.invoke(
                {"plan": Plan(sub_tasks=[_step("a"), _step("b"), _step("c")]), "work_dir": None},
                context=ctx,
                # Modest limit: if the loop were still unbounded THIS would raise.
                config={"recursion_limit": 40},
            )
        except GraphRecursionError:
            crashed = True
    finally:
        (adaptive_nodes.run_step, adaptive_nodes.adaptive_review, adaptive_nodes.replan_tail) = saved

    print(f"plan length at each executed step: {plan_lengths}")
    print(f"start=3, max reached={max(plan_lengths)}, "
          f"cap=ctx.maximum_number_of_steps_in_plan=5, "
          f"adaptations={len(final['adaptive_history'])}, crashed={crashed}")

    # FIXED: the loop reached a natural END — no recursion-limit crash.
    assert not crashed, "loop should now TERMINATE (no GraphRecursionError)"
    # FIXED: plan length never exceeds the cap at any executed step.
    assert max(plan_lengths) <= ctx.maximum_number_of_steps_in_plan, plan_lengths
    assert len(final["plan"].sub_tasks) <= ctx.maximum_number_of_steps_in_plan
    # FIXED: adaptations are bounded by the max-adaptations cap.
    assert len(final["adaptive_history"]) <= ctx.max_adaptations, final["adaptive_history"]
    print("CONFIRMED: adaptive loop is BOUNDED (plan ≤ cap, adaptations ≤ "
          "max_adaptations) and TERMINATES cleanly.")


if __name__ == "__main__":
    test_runaway_growth_is_bounded_and_terminates()
    print("adv_rt1_runaway_growth: MITIGATION VERIFIED")
