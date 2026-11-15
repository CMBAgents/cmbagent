"""Integration test for the compiled adaptive-planning graph (no LLM, no exec).

Monkeypatches the three "live" nodes so no API/execution happens:
  - `execute_step` → a fake that marks the current step fulfilled and appends a
    summary, advancing `step_index` (matches run_step's contract).
  - `adaptive_review` → returns needs_adaptation=True exactly once (after step 1),
    False afterward.
  - `replan_tail` → returns a SHORTER new_tail.

Then it builds the graph with `build_deep_research_graph()` (which looks
node functions up on the `nodes` module at build time, so the patches take
effect) and runs it on a 3-step plan. Asserts: the tail was rewritten and
shortened, the completed prefix is unchanged, and the run reaches completion.

Run:
    python tests/test_adaptive_graph.py
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


def _fake_execute_step(state, runtime):
    """Stand-in for run_step: fulfill the current step, append a summary."""
    n = state.get("step_index", 1)
    step = state["plan"].sub_tasks[n - 1]
    summaries = state.get("step_summaries", []) + [f"summary of step {n}: {step.sub_task}"]
    outcomes = state.get("step_outcomes", []) + [{"step_number": n, "fulfilled": True}]
    return {"step_summaries": summaries, "step_outcomes": outcomes, "step_index": n + 1}


def _make_fake_review():
    """needs_adaptation=True on the first call only (i.e. after step 1)."""
    calls = {"n": 0}

    def _fake_adaptive_review(state, runtime):
        calls["n"] += 1
        needs = calls["n"] == 1
        review = adaptive_schemas.AdaptiveReview(
            needs_adaptation=needs,
            recommendations=["drop the redundant step"] if needs else [],
            reason="first-step result made the tail redundant" if needs else "",
        )
        return {"adaptive_review": review}

    return _fake_adaptive_review


def _fake_replan_tail(state, runtime):
    """Replace the 2 remaining steps with a single shorter tail."""
    return {"new_tail": Plan(sub_tasks=[_step("t_final")])}


def test_adaptive_graph_rewrites_tail_and_completes():
    original = Plan(sub_tasks=[_step("s1"), _step("s2"), _step("s3")])
    original_s1 = original.sub_tasks[0].model_dump()

    # Patch the module attributes the builder reads at build time.
    saved = (
        adaptive_nodes.run_step,
        adaptive_nodes.adaptive_review,
        adaptive_nodes.replan_tail,
    )
    adaptive_nodes.run_step = _fake_execute_step
    adaptive_nodes.adaptive_review = _make_fake_review()
    adaptive_nodes.replan_tail = _fake_replan_tail
    try:
        compiled = build_deep_research_graph()
        ctx = PlanContext(main_task="integration test")
        final = compiled.invoke(
            {"plan": original, "work_dir": None}, context=ctx
        )
    finally:
        (
            adaptive_nodes.run_step,
            adaptive_nodes.adaptive_review,
            adaptive_nodes.replan_tail,
        ) = saved

    merged = final["plan"]
    # Tail rewritten + shortened: 3-step plan → prefix(1) + new_tail(1) = 2.
    assert len(merged.sub_tasks) == 2, [s.sub_task for s in merged.sub_tasks]
    # Prefix unchanged (byte-identical to the original step 1).
    assert merged.sub_tasks[0].model_dump() == original_s1
    # New tail is the replanned step, old s2/s3 gone.
    names = [s.sub_task for s in merged.sub_tasks]
    assert names == ["s1", "t_final"], names

    # Ran to completion: every executed step fulfilled, exactly 2 steps run
    # (s1, then the merged t_final), one revision recorded.
    outcomes = final["step_outcomes"]
    assert len(outcomes) == 2 and all(o["fulfilled"] for o in outcomes), outcomes
    assert len(final["adaptive_history"]) == 1, final["adaptive_history"]
    assert final["adaptive_history"][0]["after_step"] == 1
    print("ok test_adaptive_graph_rewrites_tail_and_completes")


if __name__ == "__main__":
    test_adaptive_graph_rewrites_tail_and_completes()
    print("ALL test_adaptive_graph PASSED")
