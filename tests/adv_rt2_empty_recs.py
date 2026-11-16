"""RT2 attack 5 — needs_adaptation=True but recommendations=[] is now a NO-OP.

Original vuln: the reviewer could set `needs_adaptation=True` yet return an EMPTY
`recommendations` list; the router still routed to `replan_tail`, burning a
guidance-free replanner call that could return an empty tail and chain into the
attack-1 silent truncation.

Mitigation (V8): `needs_adaptation=True` with empty `recommendations` is treated
as `needs_adaptation=False` (nothing actionable) — the router does NOT replan.

This test exercises the REAL router + the full graph.

Run: python tests/adv_rt2_empty_recs.py
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cmbagent_lg.context import PlanContext
from cmbagent_lg.planning.schemas import Plan, Step
from cmbagent_lg.deep_research import schemas as adaptive_schemas
from cmbagent_lg.deep_research import nodes as adaptive_nodes
from cmbagent_lg.deep_research.nodes import route_after_review


def _step(name):
    return Step(sub_task=name, sub_task_agent="engineer", bullet_points=[f"do {name}"])


def _plan(*names):
    return Plan(sub_tasks=[_step(n) for n in names])


# ── (a) router does NOT replan on empty recommendations ───────────────────
def test_a_empty_recs_does_not_trigger_replan():
    state = {
        "plan": _plan("s1", "s2", "s3"),
        "step_index": 2,                       # step 1 done
        "step_outcomes": [{"step_number": 1, "fulfilled": True}],
        "adaptive_review": adaptive_schemas.AdaptiveReview(
            needs_adaptation=True, recommendations=[]),   # <-- no guidance
    }
    # FIXED: no actionable guidance -> advance, not replan.
    assert route_after_review(state, runtime=None) == "run_step"
    print("ok  (a) needs_adaptation=True + empty recs -> execute_step "
          "(no wasted replanner call)")


# ── (b) full graph runs to completion; no collapse ────────────────────────
def _fake_execute_step(state, runtime):
    n = state.get("step_index", 1)
    step = state["plan"].sub_tasks[n - 1]
    return {"step_summaries": state.get("step_summaries", []) + [f"did {step.sub_task}"],
            "step_outcomes": state.get("step_outcomes", []) + [{"step_number": n, "fulfilled": True}],
            "step_index": n + 1}


def _fake_review_empty_recs(state, runtime):
    needs = len(state.get("step_outcomes", [])) == 1
    return {"adaptive_review": adaptive_schemas.AdaptiveReview(
        needs_adaptation=needs, recommendations=[])}   # True but empty


def _fake_replan_should_not_run(state, runtime):
    raise AssertionError("replan_tail must NOT run on empty recommendations")


def test_b_no_guidance_does_not_collapse_plan():
    from cmbagent_lg.deep_research.graph import build_deep_research_graph
    original = _plan("s1", "s2", "s3")
    saved = (adaptive_nodes.run_step, adaptive_nodes.adaptive_review,
             adaptive_nodes.replan_tail)
    adaptive_nodes.run_step = _fake_execute_step
    adaptive_nodes.adaptive_review = _fake_review_empty_recs
    adaptive_nodes.replan_tail = _fake_replan_should_not_run
    try:
        compiled = build_deep_research_graph()
        final = compiled.invoke({"plan": original, "work_dir": None},
                                context=PlanContext(main_task="rt2"))
    finally:
        (adaptive_nodes.run_step, adaptive_nodes.adaptive_review,
         adaptive_nodes.replan_tail) = saved

    # FIXED: empty-recs review never replans -> all 3 steps run, no collapse.
    assert [s.sub_task for s in final["plan"].sub_tasks] == ["s1", "s2", "s3"]
    assert len(final["step_outcomes"]) == 3
    # No adaptation was ever recorded.
    assert final.get("adaptive_history", []) == []
    print("ok  (b) empty-guidance review executed all 3 steps; no replan, "
          "no collapse, no history entry")


if __name__ == "__main__":
    test_a_empty_recs_does_not_trigger_replan()
    test_b_no_guidance_does_not_collapse_plan()
    print("MITIGATION VERIFIED RT2-5: needs_adaptation=True with empty "
          "recommendations is a no-op — cannot chain into truncation.")
