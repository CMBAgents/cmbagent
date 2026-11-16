"""RT3 attack 3: step_summaries / step_outcomes accounting alignment (MITIGATED).

Two probes:

A) REAL run_step, unsupported-agent branch (no LLM needed).
   deep_research/nodes.py::run_step has three branches. The engineer and
   researcher branches both append to BOTH step_summaries AND step_outcomes.

   Original vuln: the `else` (unsupported agent, e.g. "idea_maker") branch
   returned only {"step_outcomes": ..., "step_index": ...}, so the two
   accumulators DESYNCED (len(step_summaries) < len(step_outcomes)) and
   adaptive_review was fed a MISALIGNED (summary, outcome) pair.

   Mitigation (V9): the else branch now also appends a placeholder summary, so
   len(step_summaries) == len(step_outcomes) always, and the reviewer sees an
   aligned pair (both belonging to the unsupported step).

B) Full graph, aligned case: confirm that for supported (engineer) steps the two
   lists stay length-aligned and index-aligned with the plan, even across an
   adaptation that shrinks the plan.

Run:
    python tests/adv_rt3_accounting.py
"""

import os
import sys
import types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cmbagent_lg.context import PlanContext
from cmbagent_lg.planning.schemas import Plan, Step
from cmbagent_lg.deep_research import schemas as adaptive_schemas
from cmbagent_lg.deep_research import nodes as adaptive_nodes
from cmbagent_lg.deep_research.graph import build_deep_research_graph
from cmbagent_lg.deep_research.nodes import run_step as REAL_run_step


def _step(name, agent="engineer"):
    return Step(sub_task=name, sub_task_agent=agent, bullet_points=[f"do {name}"])


def probe_A_real_unsupported_branch():
    """Prove the REAL run_step now keeps summaries vs outcomes ALIGNED on an
    unsupported-agent step, so adaptive_review consumes a matched pair."""
    plan = Plan(sub_tasks=[_step("s1", "engineer"),
                           _step("s2", "idea_maker")])   # unsupported in v1

    # Pretend step 1 already ran normally: one summary, one outcome.
    state = {
        "plan": plan,
        "work_dir": None,
        "step_index": 2,                       # about to run step 2
        "step_summaries": ["sum:1:s1"],
        "step_outcomes": [{"step_number": 1, "fulfilled": True}],
    }
    out = REAL_run_step(state, types.SimpleNamespace(context=PlanContext(main_task='t')))

    # Merge the node's partial update the way langgraph would (plain replace).
    summaries = out.get("step_summaries", state["step_summaries"])
    outcomes = out.get("step_outcomes", state["step_outcomes"])

    print(f"  after unsupported step: len(summaries)={len(summaries)} "
          f"len(outcomes)={len(outcomes)}")
    assert out.get("step_index") == 3, out
    # FIXED (V9): the else branch now appends a placeholder summary too, so both
    # accumulators grew to 2 and stay length-aligned.
    assert "step_summaries" in out, (
        "unsupported branch must now return step_summaries: " + str(out)
    )
    assert len(outcomes) == 2 and len(summaries) == 2, (summaries, outcomes)

    # adaptive_review now reads an ALIGNED (summary, outcome) pair — both for the
    # unsupported step 2.
    last_summary = (summaries or ["(no summary)"])[-1]
    last_outcome = (outcomes or [{}])[-1]
    outcome_step = last_outcome["step_number"]  # == 2
    assert "Step 2" in last_summary, last_summary
    assert outcome_step == 2, last_outcome
    print(f"  ALIGNED pair fed to adaptive_review: summary→step2 "
          f"outcome→step{outcome_step}")
    print("MITIGATION VERIFIED (attack 3A): run_step unsupported-agent branch "
          "keeps step_summaries and step_outcomes aligned; adaptive_review "
          "consumes a matched (summary, outcome) pair.")


def probe_B_full_graph_alignment():
    """Engineer-only run with a shrinking adaptation stays aligned."""
    executed = []

    def fake_execute(state, runtime):
        n = state.get("step_index", 1)
        step = state["plan"].sub_tasks[n - 1]
        executed.append(n)
        return {
            "step_summaries": state.get("step_summaries", []) + [f"sum:{n}"],
            "step_outcomes": state.get("step_outcomes", [])
            + [{"step_number": n, "fulfilled": True}],
            "step_index": n + 1,
        }

    calls = {"n": 0}

    def fake_review(state, runtime):
        calls["n"] += 1
        needs = calls["n"] == 1
        return {"adaptive_review": adaptive_schemas.AdaptiveReview(
            needs_adaptation=needs, recommendations=["x"] if needs else [])}

    def fake_replan(state, runtime):
        return {"new_tail": Plan(sub_tasks=[_step("t_final")])}

    saved = (adaptive_nodes.run_step, adaptive_nodes.adaptive_review,
             adaptive_nodes.replan_tail)
    adaptive_nodes.run_step = fake_execute
    adaptive_nodes.adaptive_review = fake_review
    adaptive_nodes.replan_tail = fake_replan
    try:
        compiled = build_deep_research_graph()
        final = compiled.invoke(
            {"plan": Plan(sub_tasks=[_step("s1"), _step("s2"), _step("s3")]),
             "work_dir": None},
            context=PlanContext(main_task="rt3"))
    finally:
        (adaptive_nodes.run_step, adaptive_nodes.adaptive_review,
         adaptive_nodes.replan_tail) = saved

    summaries = final["step_summaries"]
    outcomes = final["step_outcomes"]
    plan_len = len(final["plan"].sub_tasks)
    # aligned lengths, and every outcome step_number matches an executed index
    assert len(summaries) == len(outcomes) == len(executed), (
        summaries, outcomes, executed)
    assert [o["step_number"] for o in outcomes] == executed, (outcomes, executed)
    # completed count == plan length here (prefix1 + tail1 all executed)
    assert len(outcomes) == plan_len == 2, (outcomes, plan_len)
    print(f"ok (attack 3B): engineer-only aligned "
          f"summaries={len(summaries)} outcomes={len(outcomes)} "
          f"executed={executed} plan_len={plan_len}")


if __name__ == "__main__":
    probe_A_real_unsupported_branch()
    probe_B_full_graph_alignment()
    print("adv_rt3_accounting DONE")
