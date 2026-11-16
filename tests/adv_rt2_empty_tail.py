"""RT2 attack 1 — EMPTY TAIL no longer truncates the task (now MITIGATED).

Original vuln: a degenerate replanner emitting a Plan with ZERO steps made
`merge_plan` collapse the plan to just the completed prefix, so a multi-step task
ENDed early as if COMPLETE — silent data loss.

Mitigation (V4/V2): an empty replan tail is treated as a NO-OP adaptation — the
EXISTING remaining tail is kept unchanged; the plan is never collapsed to just
the prefix.

This test now asserts the FIXED behavior, two ways:
  (A) direct `merge_plan` unit call keeps the remaining steps, and
  (B) the full compiled graph runs every step to completion.

Run: python tests/adv_rt2_empty_tail.py
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cmbagent_lg.context import PlanContext
from cmbagent_lg.planning.schemas import Plan, Step
from cmbagent_lg.deep_research import schemas as adaptive_schemas
from cmbagent_lg.deep_research import nodes as adaptive_nodes
from cmbagent_lg.deep_research.nodes import merge_plan, route_after_merge
from cmbagent_lg.deep_research.graph import build_deep_research_graph


def _step(name):
    return Step(sub_task=name, sub_task_agent="engineer", bullet_points=[f"do {name}"])


def _plan(*names):
    return Plan(sub_tasks=[_step(n) for n in names])


# ── (A) direct merge_plan: empty new_tail keeps the existing remaining tail ──
def test_A_merge_keeps_remaining_tail():
    original = _plan("s1", "s2", "s3", "s4")
    state = {
        "plan": original,
        "step_index": 3,                     # 2 steps completed
        "new_tail": Plan(sub_tasks=[]),      # <-- degenerate: zero steps
        "adaptive_review": adaptive_schemas.AdaptiveReview(
            needs_adaptation=True, recommendations=["(garbage) rewrite tail"]
        ),
        "adaptive_history": [],
    }
    out = merge_plan(state, runtime=None)
    merged = out["plan"]

    # FIXED: the remaining steps s3 & s4 survive — no collapse.
    names = [s.sub_task for s in merged.sub_tasks]
    assert names == ["s1", "s2", "s3", "s4"], names
    assert "s3" in names and "s4" in names

    # Router: steps still remain -> continue executing (not END).
    route = route_after_merge({"plan": merged, "step_index": 3}, runtime=None)
    assert route == "run_step", route
    print("ok  (A) merge_plan keeps remaining s3&s4 on empty replan; continues")


# ── (B) full graph: run executes every step to completion ──────────────────
def _fake_execute_step(state, runtime):
    n = state.get("step_index", 1)
    step = state["plan"].sub_tasks[n - 1]
    return {
        "step_summaries": state.get("step_summaries", []) + [f"did {step.sub_task}"],
        "step_outcomes": state.get("step_outcomes", []) + [{"step_number": n, "fulfilled": True}],
        "step_index": n + 1,
    }


def _fake_review_once(state, runtime):
    # adapt exactly once, after step 1 (with an actionable recommendation)
    n = len(state.get("step_outcomes", []))
    needs = n == 1
    return {"adaptive_review": adaptive_schemas.AdaptiveReview(
        needs_adaptation=needs, recommendations=["junk"] if needs else [])}


def _fake_replan_empty(state, runtime):
    return {"new_tail": Plan(sub_tasks=[])}   # <-- the degenerate output


def test_B_full_graph_completes_all_steps():
    original = _plan("s1", "s2", "s3", "s4")
    saved = (adaptive_nodes.run_step, adaptive_nodes.adaptive_review,
             adaptive_nodes.replan_tail)
    adaptive_nodes.run_step = _fake_execute_step
    adaptive_nodes.adaptive_review = _fake_review_once
    adaptive_nodes.replan_tail = _fake_replan_empty
    try:
        compiled = build_deep_research_graph()
        final = compiled.invoke({"plan": original, "work_dir": None},
                                context=PlanContext(main_task="rt2"))
    finally:
        (adaptive_nodes.run_step, adaptive_nodes.adaptive_review,
         adaptive_nodes.replan_tail) = saved

    outcomes = final["step_outcomes"]
    # FIXED: all 4 steps run; nothing silently lost.
    assert all(o["fulfilled"] for o in outcomes), outcomes
    assert len(outcomes) == 4, [o["step_number"] for o in outcomes]
    assert [s.sub_task for s in final["plan"].sub_tasks] == ["s1", "s2", "s3", "s4"]
    print(f"ok  (B) full graph completed all {len(outcomes)} steps "
          f"(empty replan was a no-op, nothing lost)")


if __name__ == "__main__":
    test_A_merge_keeps_remaining_tail()
    test_B_full_graph_completes_all_steps()
    print("MITIGATION VERIFIED RT2-1: empty replan tail is a no-op, not a truncation.")
