"""Termination tests for the adaptive-planning caps (feature R-02).

The adaptive loop is bounded by two INDEPENDENT backstops:
  1. plan-length cap  — `merge_plan` truncates the merged tail to
     `ctx.maximum_number_of_steps_in_plan` (never the frozen prefix); and
  2. max-adaptations  — `route_after_review` stops routing to `replan_tail`
     once `len(adaptive_history)` reaches `ctx.max_adaptations`.

Under an ALWAYS-adapt reviewer + an ALWAYS-grow planner, either cap alone forces
termination. These tests assert the loop terminates and both bounds hold, and
that each cap is independently effective.

Run:
    python tests/test_adaptive_caps.py
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


def _always_execute(state, runtime):
    n = state.get("step_index", 1)
    return {
        "step_summaries": state.get("step_summaries", []) + [f"summary {n}"],
        "step_outcomes": state.get("step_outcomes", []) + [{"step_number": n, "fulfilled": True}],
        "step_index": n + 1,
    }


def _always_adapt(state, runtime):
    return {"adaptive_review": adaptive_schemas.AdaptiveReview(
        needs_adaptation=True, recommendations=["grow"], reason="adversarial")}


def _always_grow(state, runtime):
    # A tail one step longer than what remains — the runaway-growth attack.
    n_done = state.get("step_index", 1) - 1
    remaining = state["plan"].sub_tasks[n_done:]
    return {"new_tail": Plan(sub_tasks=[_step(f"t{i}") for i in range(len(remaining) + 1)])}


def _run(ctx, start_names, recursion_limit=60):
    saved = (adaptive_nodes.run_step, adaptive_nodes.adaptive_review,
             adaptive_nodes.replan_tail)
    adaptive_nodes.run_step = _always_execute
    adaptive_nodes.adaptive_review = _always_adapt
    adaptive_nodes.replan_tail = _always_grow
    try:
        compiled = build_deep_research_graph()
        return compiled.invoke(
            {"plan": Plan(sub_tasks=[_step(n) for n in start_names]), "work_dir": None},
            context=ctx, config={"recursion_limit": recursion_limit})
    finally:
        (adaptive_nodes.run_step, adaptive_nodes.adaptive_review,
         adaptive_nodes.replan_tail) = saved


def test_both_caps_terminate():
    """Always-adapt + always-grow terminates; plan ≤ cap and adaptations ≤ cap."""
    ctx = PlanContext(main_task="caps", maximum_number_of_steps_in_plan=5,
                      max_adaptations=3)
    final = _run(ctx, ["a", "b", "c"])
    plan_len = len(final["plan"].sub_tasks)
    n_adapt = len(final["adaptive_history"])
    assert plan_len <= ctx.maximum_number_of_steps_in_plan, plan_len
    assert n_adapt <= ctx.max_adaptations, n_adapt
    # Ran to a natural END (would have raised GraphRecursionError otherwise).
    assert final["step_index"] > plan_len
    print(f"ok test_both_caps_terminate: plan_len={plan_len}<=5, "
          f"adaptations={n_adapt}<=3")


def test_max_adaptations_cap_alone():
    """With a generous length cap, the max-adaptations cap alone halts growth."""
    ctx = PlanContext(main_task="adapt-cap", maximum_number_of_steps_in_plan=1000,
                      max_adaptations=2)
    final = _run(ctx, ["a", "b"], recursion_limit=60)
    n_adapt = len(final["adaptive_history"])
    assert n_adapt <= ctx.max_adaptations, n_adapt
    # After max_adaptations, growth stops and step_index marches to the end.
    assert final["step_index"] > len(final["plan"].sub_tasks)
    print(f"ok test_max_adaptations_cap_alone: adaptations={n_adapt}<=2 "
          f"(length cap generous)")


def test_length_cap_alone():
    """With a generous adaptation cap, the length cap alone bounds the plan."""
    ctx = PlanContext(main_task="len-cap", maximum_number_of_steps_in_plan=4,
                      max_adaptations=1000)
    final = _run(ctx, ["a", "b"], recursion_limit=80)
    plan_len = len(final["plan"].sub_tasks)
    assert plan_len <= ctx.maximum_number_of_steps_in_plan, plan_len
    assert final["step_index"] > plan_len
    print(f"ok test_length_cap_alone: plan_len={plan_len}<=4 "
          f"(adaptation cap generous)")


def test_no_recursion_error_under_default_limit():
    """The runaway driver must not raise GraphRecursionError under a modest limit."""
    ctx = PlanContext(main_task="no-crash", maximum_number_of_steps_in_plan=5,
                      max_adaptations=3)
    raised = False
    try:
        _run(ctx, ["a", "b", "c"], recursion_limit=40)
    except GraphRecursionError:
        raised = True
    assert not raised, "adaptive loop must terminate without a recursion error"
    print("ok test_no_recursion_error_under_default_limit")


if __name__ == "__main__":
    test_both_caps_terminate()
    test_max_adaptations_cap_alone()
    test_length_cap_alone()
    test_no_recursion_error_under_default_limit()
    print("ALL test_adaptive_caps PASSED")
