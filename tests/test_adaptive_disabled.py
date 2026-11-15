"""Disabled-path test for adaptive planning (feature R-02).

Adaptive planning is ON BY DEFAULT inside deep_research. Setting
`PlanContext.adaptive_planning=False` must reproduce the OLD linear behaviour
with ZERO extra LLM calls: `adaptive_review` early-returns without touching any
model, `route_after_review` reduces to the linear `after_step`, and a multi-step
run executes exactly like the linear graph did.

This test drives the REAL compiled deep_research graph (adaptive branch wired but
inert) with a fake `run_step` (no API, no execution). It asserts:
  (A) NO adaptive/formatter LLM call is ever made — `chat_model` is stubbed to
      raise so any adaptive reasoning/formatting call would blow up; and
  (B) the run behaves exactly like the linear graph: every step runs once, in
      order, all fulfilled, the plan is untouched, and no adaptive state
      (adaptive_review / new_tail / adaptive_history) is ever produced.

For contrast a sibling check confirms that with adaptive ENABLED the same driver
DOES reach the reviewer (so the disabled result is a real gate, not a no-op that
always holds).

Run:
    python tests/test_adaptive_disabled.py
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cmbagent_lg.context import PlanContext
from cmbagent_lg.planning.schemas import Plan, Step
from cmbagent_lg.deep_research import nodes as dr_nodes
from cmbagent_lg.deep_research.graph import build_deep_research_graph


def _step(name: str) -> Step:
    return Step(sub_task=name, sub_task_agent="engineer", bullet_points=[f"do {name}"])


def _fake_run_step(state, runtime):
    """Stand-in for run_step: fulfill the current step, append a summary/outcome."""
    n = state.get("step_index", 1)
    step = state["plan"].sub_tasks[n - 1]
    return {
        "step_summaries": state.get("step_summaries", []) + [f"summary {n}: {step.sub_task}"],
        "step_outcomes": state.get("step_outcomes", []) + [{"step_number": n, "fulfilled": True}],
        "step_index": n + 1,
    }


def _boom_chat_model(*a, **k):
    raise AssertionError(
        "chat_model must NOT be called on the disabled path — an adaptive "
        f"reasoning/formatter LLM call leaked (args={a})"
    )


def _run(ctx, names):
    """Run the REAL graph with run_step faked and chat_model booby-trapped."""
    saved_run = dr_nodes.run_step
    saved_chat = dr_nodes.chat_model
    dr_nodes.run_step = _fake_run_step
    dr_nodes.chat_model = _boom_chat_model
    calls = {"chat": 0}
    try:
        compiled = build_deep_research_graph()
        final = compiled.invoke(
            {"plan": Plan(sub_tasks=[_step(n) for n in names]), "work_dir": None},
            context=ctx,
        )
        return final
    finally:
        dr_nodes.run_step = saved_run
        dr_nodes.chat_model = saved_chat


def test_disabled_makes_zero_adaptive_calls_and_matches_linear():
    ctx = PlanContext(main_task="disabled", adaptive_planning=False,
                      maximum_number_of_steps_in_plan=5)
    final = _run(ctx, ["s1", "s2", "s3"])

    # (B) behaves exactly like the linear graph.
    names = [s.sub_task for s in final["plan"].sub_tasks]
    assert names == ["s1", "s2", "s3"], names                  # plan untouched
    outcomes = final["step_outcomes"]
    assert len(outcomes) == 3 and all(o["fulfilled"] for o in outcomes), outcomes
    assert [o["step_number"] for o in outcomes] == [1, 2, 3], outcomes
    assert len(final["step_summaries"]) == 3, final["step_summaries"]
    # No adaptive state ever materialised (byte-identical to linear deep_research).
    assert final.get("adaptive_review") is None, final.get("adaptive_review")
    assert final.get("new_tail") is None, final.get("new_tail")
    assert final.get("raw_tail") is None, final.get("raw_tail")
    assert final.get("adaptive_history", []) == [], final.get("adaptive_history")
    # (A) is enforced by _boom_chat_model raising if any LLM call were made — the
    # run reaching here at all proves ZERO adaptive/formatter calls.
    print("ok test_disabled_makes_zero_adaptive_calls_and_matches_linear: "
          "3 steps ran linearly, no adaptive state, zero LLM calls")


def test_enabled_would_reach_the_reviewer():
    """Sanity contrast: with adaptive ON, the same driver DOES call the reviewer
    (so the disabled gate above is a real gate, not a vacuous no-op)."""
    ctx = PlanContext(main_task="enabled", adaptive_planning=True,
                      maximum_number_of_steps_in_plan=5)
    reached = {"n": 0}

    saved_run = dr_nodes.run_step
    saved_chat = dr_nodes.chat_model

    def _count_chat(*a, **k):
        reached["n"] += 1
        raise RuntimeError("stop here — we only needed to prove the call happens")

    dr_nodes.run_step = _fake_run_step
    dr_nodes.chat_model = _count_chat
    try:
        compiled = build_deep_research_graph()
        try:
            compiled.invoke(
                {"plan": Plan(sub_tasks=[_step("s1"), _step("s2")]), "work_dir": None},
                context=ctx,
            )
        except RuntimeError:
            pass  # expected — the reviewer's reasoning call fired
    finally:
        dr_nodes.run_step = saved_run
        dr_nodes.chat_model = saved_chat

    assert reached["n"] >= 1, "adaptive-enabled run should reach the reviewer LLM call"
    print(f"ok test_enabled_would_reach_the_reviewer: reviewer LLM call fired "
          f"({reached['n']}x) — disabled gate is real")


if __name__ == "__main__":
    test_disabled_makes_zero_adaptive_calls_and_matches_linear()
    test_enabled_would_reach_the_reviewer()
    print("ALL test_adaptive_disabled PASSED")
