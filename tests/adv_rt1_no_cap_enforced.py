"""RT1 / TERMINATION attack #3 — CAP is now ENFORCED on the adaptive path.

Originally NOTHING bounded the adaptive loop. Post-mitigation two independent
caps do, and this test verifies both:

(A) STATIC: inspect the source of deep_research/nodes.py and prove that the
    plan-size cap (`maximum_number_of_steps_in_plan`) IS referenced by
    `merge_plan` and that `route_after_review` consults an adaptation counter
    (`adaptive_history` length vs `max_adaptations`).

(B) DYNAMIC: force a burst of growth adaptations and show the merged plan is
    CLAMPED at ctx.maximum_number_of_steps_in_plan — it never sails past the cap.

Run:  python tests/adv_rt1_no_cap_enforced.py
"""

import inspect
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


def test_static_cap_in_source():
    src = inspect.getsource(adaptive_nodes)
    # The plan-size cap field is now consulted in the adaptive nodes.
    assert "maximum_number_of_steps_in_plan" in src, \
        "expected the plan-size cap to be enforced in the adaptive nodes"
    # merge_plan enforces the length cap.
    merge_src = inspect.getsource(adaptive_nodes.merge_plan)
    assert "maximum_number_of_steps_in_plan" in merge_src, merge_src
    # route_after_review consults an adaptation counter as a second backstop.
    review_router = inspect.getsource(adaptive_nodes.route_after_review)
    assert "adaptive_history" in review_router and "max_adaptations" in review_router, \
        review_router
    print("STATIC: merge_plan enforces the plan-size cap; route_after_review "
          "gates on adaptive_history vs max_adaptations.")


def test_dynamic_plan_clamped_at_cap():
    # Reviewer keeps asking to grow; the merged plan must be clamped at the cap.
    def _execute_step(state, runtime):
        n = state.get("step_index", 1)
        return {
            "step_summaries": state.get("step_summaries", []) + [f"summary {n}"],
            "step_outcomes": state.get("step_outcomes", []) + [{"step_number": n, "fulfilled": True}],
            "step_index": n + 1,
        }

    def _adaptive_review(state, runtime):
        return {"adaptive_review": adaptive_schemas.AdaptiveReview(
            needs_adaptation=True, recommendations=["grow"])}

    def _replan_tail(state, runtime):
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
        ctx = PlanContext(main_task="cap-test", maximum_number_of_steps_in_plan=5,
                          max_adaptations=3)
        final = compiled.invoke(
            {"plan": Plan(sub_tasks=[_step("a"), _step("b")]), "work_dir": None},
            context=ctx,
            config={"recursion_limit": 200},
        )
    finally:
        (adaptive_nodes.run_step, adaptive_nodes.adaptive_review, adaptive_nodes.replan_tail) = saved

    final_len = len(final["plan"].sub_tasks)
    n_adapt = len(final["adaptive_history"])
    print(f"cap=ctx.maximum_number_of_steps_in_plan=5 ; final plan length={final_len} ; "
          f"adaptations applied={n_adapt}")
    assert final_len <= ctx.maximum_number_of_steps_in_plan, \
        f"plan length {final_len} must not exceed the cap {ctx.maximum_number_of_steps_in_plan}"
    assert n_adapt <= ctx.max_adaptations, n_adapt
    print("DYNAMIC: merged plan clamped at the cap — growth is bounded.")


if __name__ == "__main__":
    test_static_cap_in_source()
    test_dynamic_plan_clamped_at_cap()
    print("adv_rt1_no_cap_enforced: MITIGATION VERIFIED (cap enforced on adaptive path)")
