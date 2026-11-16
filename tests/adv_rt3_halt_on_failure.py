"""RT3 attack 4: a completed step with fulfilled=False must HALT, never adapt.

route_after_review checks, IN ORDER:
    1. outcomes[-1].fulfilled is False  -> END      (failure wins)
    2. step_index > len(plan)           -> END      (exhausted)
    3. review.needs_adaptation          -> replan_tail
    4. else                             -> execute_step

The attack: make the step FAIL *and* make the reviewer scream
needs_adaptation=True. If the failure check did not come first (or the adaptive
router dropped the halt-on-failure that deep_research's `after_step` has), the
run would wrongly proceed into replan_tail/merge_plan on top of a failed step.

We instrument replan_tail / merge_plan to record whether they ever fire, and
count executed steps, then assert: on failure the graph ENDs, no replan, no
merge, and no step past the failing one runs.

Run:
    python tests/adv_rt3_halt_on_failure.py
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cmbagent_lg.context import PlanContext
from cmbagent_lg.planning.schemas import Plan, Step
from cmbagent_lg.deep_research import schemas as adaptive_schemas
from cmbagent_lg.deep_research import nodes as adaptive_nodes
from cmbagent_lg.deep_research.graph import build_deep_research_graph


def _step(name):
    return Step(sub_task=name, sub_task_agent="engineer", bullet_points=[f"do {name}"])


def run(fail_on_step):
    executed = []
    fired = {"replan": 0, "merge": 0}

    def fake_execute(state, runtime):
        n = state.get("step_index", 1)
        executed.append(n)
        fulfilled = n != fail_on_step
        return {
            "step_summaries": state.get("step_summaries", []) + [f"sum:{n}"],
            "step_outcomes": state.get("step_outcomes", [])
            + [{"step_number": n, "fulfilled": fulfilled,
                "reason": None if fulfilled else "boom"}],
            "step_index": n + 1,
        }

    def fake_review(state, runtime):
        # Always demand adaptation — the trap.
        return {"adaptive_review": adaptive_schemas.AdaptiveReview(
            needs_adaptation=True, recommendations=["adapt!"])}

    # Wrap the REAL merge / replan so we can detect if they ever run, but they
    # never should on the failing path.
    real_replan = adaptive_nodes.replan_tail
    real_merge = adaptive_nodes.merge_plan

    def spy_replan(state, runtime):
        fired["replan"] += 1
        return {"new_tail": Plan(sub_tasks=[_step("SHOULD_NOT_RUN")])}

    def spy_merge(state, runtime):
        fired["merge"] += 1
        return real_merge(state, runtime)

    saved = (adaptive_nodes.run_step, adaptive_nodes.adaptive_review,
             adaptive_nodes.replan_tail, adaptive_nodes.merge_plan)
    adaptive_nodes.run_step = fake_execute
    adaptive_nodes.adaptive_review = fake_review
    adaptive_nodes.replan_tail = spy_replan
    adaptive_nodes.merge_plan = spy_merge
    try:
        compiled = build_deep_research_graph()
        final = compiled.invoke(
            {"plan": Plan(sub_tasks=[_step("s1"), _step("s2"), _step("s3")]),
             "work_dir": None},
            context=PlanContext(main_task="rt3"))
    finally:
        (adaptive_nodes.run_step, adaptive_nodes.adaptive_review,
         adaptive_nodes.replan_tail, adaptive_nodes.merge_plan) = saved
    return executed, fired, final


def main():
    # Fail on the FIRST step: must halt immediately, no replan/merge, only 1 run.
    executed, fired, final = run(fail_on_step=1)
    assert executed == [1], f"steps past a failed step ran: {executed}"
    assert fired == {"replan": 0, "merge": 0}, f"adapted after failure: {fired}"
    assert final["step_outcomes"][-1]["fulfilled"] is False
    # plan untouched — SHOULD_NOT_RUN never spliced in
    assert [s.sub_task for s in final["plan"].sub_tasks] == ["s1", "s2", "s3"]
    print(f"ok fail-on-first: executed={executed} fired={fired} "
          "(HALT preserved, no adaptation)")

    # Fail on the MIDDLE step: step1 ok (and would trigger adaptation!), step2
    # fails -> must halt at 2, having adapted only after step1.
    executed, fired, final = run(fail_on_step=2)
    assert executed[:2] == [1, 2], executed
    # step 3 (or any replanned tail step) must NEVER execute after the failure
    assert len(executed) == len(set(executed)), f"re-execution: {executed}"
    assert max(executed) == 2, f"ran past failed step 2: {executed}"
    # exactly one adaptation happened (after the successful step 1), and none
    # after the failed step 2.
    assert fired["replan"] == 1 and fired["merge"] == 1, fired
    assert final["step_outcomes"][-1]["fulfilled"] is False
    print(f"ok fail-on-middle: executed={executed} fired={fired} "
          "(halted at failed step; no post-failure adaptation)")

    print("ALL adv_rt3_halt_on_failure PASSED (halt-on-failure preserved)")


if __name__ == "__main__":
    main()
