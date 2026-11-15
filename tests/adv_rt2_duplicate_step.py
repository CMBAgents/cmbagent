"""RT2 attack 3 — RE-ADDED completed step is now DROPPED, never re-executed.

Invariant ("completed steps never redone" — graph.py docstring).

Original vuln: the replanner's new tail could re-include a step byte-identical to
one already in the frozen prefix; `merge_plan` blindly concatenated
`completed_prefix + new_tail`, so the re-added step sat exactly at the execution
pointer and got run a SECOND time.

Mitigation (V6): at merge time any new-tail step content-identical (same
sub_task, agent, bullet_points) to a completed prefix step is dropped, so
already-done work is never re-executed.

This test now asserts the FIXED behavior, two ways:
  (A) direct merge_plan de-dupes the re-added completed step, and
  (B) the full graph executes the completed step exactly ONCE.

Run: python tests/adv_rt2_duplicate_step.py
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cmbagent_lg.context import PlanContext
from cmbagent_lg.planning.schemas import Plan, Step
from cmbagent_lg.deep_research import schemas as adaptive_schemas
from cmbagent_lg.deep_research import nodes as adaptive_nodes
from cmbagent_lg.deep_research.nodes import merge_plan


def _step(name):
    return Step(sub_task=name, sub_task_agent="engineer", bullet_points=[f"do {name}"])


def _plan(*names):
    return Plan(sub_tasks=[_step(n) for n in names])


# ── (A) merge_plan de-dupes: prefix step is stripped from the tail ─────────
def test_A_merge_drops_readded_completed_step():
    original = _plan("build_dataset", "train_model", "evaluate")
    # 1 step completed (build_dataset). Replanner re-adds an IDENTICAL
    # "build_dataset" as the first tail step.
    new_tail = _plan("build_dataset", "train_model_v2")
    state = {
        "plan": original,
        "step_index": 2,                       # step 1 (build_dataset) done
        "step_outcomes": [{"step_number": 1, "fulfilled": True}],
        "new_tail": new_tail,
        "adaptive_review": adaptive_schemas.AdaptiveReview(
            needs_adaptation=True, recommendations=["redo the dataset build"]),
        "adaptive_history": [],
    }
    merged = merge_plan(state, runtime=None)["plan"]
    names = [s.sub_task for s in merged.sub_tasks]
    # FIXED: "build_dataset" appears exactly once (the frozen prefix step); the
    # re-added duplicate was stripped from the tail.
    assert names.count("build_dataset") == 1, names
    assert names == ["build_dataset", "train_model_v2"], names
    # The step the pointer will run next is NOT the completed one.
    assert merged.sub_tasks[state["step_index"] - 1].sub_task == "train_model_v2"
    print(f"ok  (A) merged plan de-duped the re-added completed step: {names}")


# ── (B) full graph: the completed step runs exactly once ──────────────────
_EXECUTED = []


def _spy_execute_step(state, runtime):
    n = state.get("step_index", 1)
    step = state["plan"].sub_tasks[n - 1]
    _EXECUTED.append((n, step.sub_task))
    return {
        "step_summaries": state.get("step_summaries", []) + [f"did {step.sub_task}"],
        "step_outcomes": state.get("step_outcomes", []) + [{"step_number": n, "fulfilled": True}],
        "step_index": n + 1,
    }


def _review_once(state, runtime):
    needs = len(state.get("step_outcomes", [])) == 1   # adapt after step 1
    return {"adaptive_review": adaptive_schemas.AdaptiveReview(
        needs_adaptation=needs, recommendations=["redo dataset"] if needs else [])}


def _replan_readds(state, runtime):
    # re-include the already-completed step 1 verbatim, then a genuinely new one
    return {"new_tail": _plan("build_dataset", "train_model_v2")}


def test_B_full_graph_runs_completed_step_once():
    _EXECUTED.clear()
    original = _plan("build_dataset", "train_model", "evaluate")
    saved = (adaptive_nodes.run_step, adaptive_nodes.adaptive_review,
             adaptive_nodes.replan_tail)
    adaptive_nodes.run_step = _spy_execute_step
    adaptive_nodes.adaptive_review = _review_once
    adaptive_nodes.replan_tail = _replan_readds
    try:
        from cmbagent_lg.deep_research.graph import build_deep_research_graph
        compiled = build_deep_research_graph()
        compiled.invoke({"plan": original, "work_dir": None},
                        context=PlanContext(main_task="rt2"))
    finally:
        (adaptive_nodes.run_step, adaptive_nodes.adaptive_review,
         adaptive_nodes.replan_tail) = saved

    ran = [name for _, name in _EXECUTED]
    # FIXED: build_dataset executed exactly ONCE.
    assert ran.count("build_dataset") == 1, ran
    assert ran == ["build_dataset", "train_model_v2"], ran
    print(f"ok  (B) execution trace ran the completed step once: {ran}")


if __name__ == "__main__":
    test_A_merge_drops_readded_completed_step()
    test_B_full_graph_runs_completed_step_once()
    print("MITIGATION VERIFIED RT2-3: a re-added completed step is dropped "
          "(preserves 'completed steps never redone').")
