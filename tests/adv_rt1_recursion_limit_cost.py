"""RT1 / TERMINATION attack #4 — the recursion_limit is no longer the backstop.

Originally the only thing stopping runaway growth was LangGraph's enormous
default recursion_limit (~10007), so a runaway loop burned ~thousands of paid
model-call rounds before aborting. Post-mitigation the loop is bounded by the two
caps (plan length + max_adaptations), so it TERMINATES on its own in a handful of
rounds — long before any recursion limit is relevant.

Findings this repro now pins down:
  1. The same always-adapt / always-grow driver from attack #1, run under the
     REAL (huge) default recursion_limit, terminates cleanly after only a FEW
     adaptation rounds (bounded by max_adaptations) — NOT thousands.
  2. A realistic non-adaptive 12-step plan still completes comfortably.

Both sub-tests use trivial in-process fakes (no API).

Run:  python tests/adv_rt1_recursion_limit_cost.py
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from langgraph.errors import GraphRecursionError
from langgraph._internal._config import DEFAULT_RECURSION_LIMIT

from cmbagent_lg.context import PlanContext
from cmbagent_lg.planning.schemas import Plan, Step
from cmbagent_lg.deep_research import schemas as adaptive_schemas
from cmbagent_lg.deep_research import nodes as adaptive_nodes
from cmbagent_lg.deep_research.graph import build_deep_research_graph


def _step(name: str) -> Step:
    return Step(sub_task=name, sub_task_agent="engineer", bullet_points=[f"do {name}"])


def _fulfill(state, runtime):
    n = state.get("step_index", 1)
    return {
        "step_summaries": state.get("step_summaries", []) + [f"summary {n}"],
        "step_outcomes": state.get("step_outcomes", []) + [{"step_number": n, "fulfilled": True}],
        "step_index": n + 1,
    }


def test_runaway_now_terminates_in_few_rounds():
    print(f"DEFAULT_RECURSION_LIMIT = {DEFAULT_RECURSION_LIMIT}")
    assert DEFAULT_RECURSION_LIMIT >= 1000, DEFAULT_RECURSION_LIMIT

    rounds = {"replans": 0}

    def _adaptive_review(state, runtime):
        return {"adaptive_review": adaptive_schemas.AdaptiveReview(
            needs_adaptation=True, recommendations=["grow"])}

    def _replan_tail(state, runtime):
        rounds["replans"] += 1
        n_done = state.get("step_index", 1) - 1
        remaining = state["plan"].sub_tasks[n_done:]
        return {"new_tail": Plan(sub_tasks=[_step(f"t{i}") for i in range(len(remaining) + 1)])}

    saved = (adaptive_nodes.run_step, adaptive_nodes.adaptive_review, adaptive_nodes.replan_tail)
    adaptive_nodes.run_step = _fulfill
    adaptive_nodes.adaptive_review = _adaptive_review
    adaptive_nodes.replan_tail = _replan_tail
    try:
        compiled = build_deep_research_graph()
        ctx = PlanContext(main_task="cost", maximum_number_of_steps_in_plan=5,
                          max_adaptations=3)
        crashed = False
        try:
            # Use the REAL default limit — the loop must stop on its own well
            # before it, purely from the two caps.
            final = compiled.invoke(
                {"plan": Plan(sub_tasks=[_step("a"), _step("b"), _step("c")]), "work_dir": None},
                context=ctx,
                config={"recursion_limit": DEFAULT_RECURSION_LIMIT},
            )
        except GraphRecursionError:
            crashed = True
    finally:
        (adaptive_nodes.run_step, adaptive_nodes.adaptive_review, adaptive_nodes.replan_tail) = saved

    print(f"bounded loop: adaptation rounds executed before natural END = "
          f"{rounds['replans']}")
    assert not crashed, "loop must terminate WITHOUT hitting the recursion limit"
    # No longer thousands: bounded by max_adaptations.
    assert rounds["replans"] <= ctx.max_adaptations, rounds["replans"]
    assert len(final["adaptive_history"]) <= ctx.max_adaptations
    print("CONFIRMED: runaway driver now terminates in a few bounded rounds — "
          "the recursion_limit is no longer the safety net.")


def test_realistic_12step_plan_completes_when_healthy():
    # No adaptation — a normal 12-step plan finishes; recursion_limit is not the
    # limiting factor for a healthy run.
    def _no_adapt(state, runtime):
        return {"adaptive_review": adaptive_schemas.AdaptiveReview(needs_adaptation=False)}

    saved = (adaptive_nodes.run_step, adaptive_nodes.adaptive_review, adaptive_nodes.replan_tail)
    adaptive_nodes.run_step = _fulfill
    adaptive_nodes.adaptive_review = _no_adapt
    try:
        compiled = build_deep_research_graph()
        ctx = PlanContext(main_task="12-step", maximum_number_of_steps_in_plan=12)
        plan = Plan(sub_tasks=[_step(f"s{i}") for i in range(1, 13)])
        final = compiled.invoke({"plan": plan, "work_dir": None}, context=ctx)  # default limit
    finally:
        (adaptive_nodes.run_step, adaptive_nodes.adaptive_review, adaptive_nodes.replan_tail) = saved

    ran = len(final["step_outcomes"])
    print(f"healthy non-adaptive 12-step plan: steps executed = {ran} (completed cleanly)")
    assert ran == 12, ran


if __name__ == "__main__":
    test_runaway_now_terminates_in_few_rounds()
    test_realistic_12step_plan_completes_when_healthy()
    print("adv_rt1_recursion_limit_cost: MITIGATION VERIFIED (bounded loop + healthy baseline)")
