"""RT2 attack 2 — INVALID / TYPO agent in a replanned tail is now REJECTED.

Original vuln: `merge_plan` round-tripped the replanned tail through the STATIC
`Plan`/`Step` (whose `sub_task_agent` is a free `str`, no re-validation), so a
degenerate replan naming an unknown agent (typo "engineeer", hallucinated
"plotter") got spliced verbatim. When such a step later reached `run_step` it
fell into the unsupported-agent branch, recorded fulfilled=False, and HALTED
every remaining legitimate step.

Mitigation (V5): at merge time every new-tail step's `sub_task_agent` is
validated against the available-agent set (derived the same way the
planner/`_build_dynamic_plan_model` does). If ANY step names an unknown agent,
the whole replan is REJECTED and the prior remaining tail is kept — the bad step
never enters the plan.

This test exercises the REAL `merge_plan` and asserts the safe outcome.

Run: python tests/adv_rt2_invalid_agent.py
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cmbagent_lg.context import PlanContext
from cmbagent_lg.planning.schemas import Plan, Step
from cmbagent_lg.deep_research.schemas import AdaptiveReview
from cmbagent_lg.deep_research.nodes import merge_plan


class _Runtime:
    def __init__(self, ctx):
        self.context = ctx


def _step(name, agent="engineer"):
    return Step(sub_task=name, sub_task_agent=agent, bullet_points=[f"do {name}"])


def _plan(*steps):
    return Plan(sub_tasks=list(steps))


def test_typo_agent_replan_rejected():
    # 1 step done (compute). Remaining is [plot, write]. The replan tries to
    # splice a step with a typo'd agent — it must be rejected wholesale.
    original = _plan(_step("compute"), _step("plot"), _step("write", "researcher"))
    bad_tail = _plan(
        _step("recompute", "engineeer"),          # <-- typo agent
        _step("replot", "engineer"),
    )
    state = {
        "plan": original,
        "step_index": 2,                          # step 1 done
        "step_outcomes": [{"step_number": 1, "fulfilled": True}],
        "new_tail": bad_tail,
        "adaptive_review": AdaptiveReview(needs_adaptation=True,
                                          recommendations=["redo it"]),
        "adaptive_history": [],
    }
    merged = merge_plan(state, _Runtime(PlanContext(main_task="rt2")))["plan"]
    names = [(s.sub_task, s.sub_task_agent) for s in merged.sub_tasks]
    # FIXED: the bad tail is rejected; the prior remaining tail is preserved.
    assert names == [("compute", "engineer"), ("plot", "engineer"),
                     ("write", "researcher")], names
    # No step with an unknown agent ever entered the plan.
    assert all(s.sub_task_agent in {"engineer", "researcher"}
               for s in merged.sub_tasks), names
    print("ok  typo agent 'engineeer' replan REJECTED; prior tail kept intact")


def test_hallucinated_agent_replan_rejected():
    original = _plan(_step("draw"), _step("next"))
    bad_tail = _plan(_step("render", "plotter"))   # not in available set
    state = {
        "plan": original,
        "step_index": 2,                           # step 1 done
        "step_outcomes": [{"step_number": 1, "fulfilled": True}],
        "new_tail": bad_tail,
        "adaptive_review": AdaptiveReview(needs_adaptation=True,
                                          recommendations=["use plotter"]),
        "adaptive_history": [],
    }
    merged = merge_plan(state, _Runtime(PlanContext(main_task="rt2")))["plan"]
    names = [s.sub_task for s in merged.sub_tasks]
    # FIXED: hallucinated "plotter" tail rejected; remaining ["next"] kept.
    assert names == ["draw", "next"], names
    assert all(s.sub_task_agent in {"engineer", "researcher"}
               for s in merged.sub_tasks)
    print("ok  hallucinated agent 'plotter' replan REJECTED; remaining kept")


if __name__ == "__main__":
    test_typo_agent_replan_rejected()
    test_hallucinated_agent_replan_rejected()
    print("MITIGATION VERIFIED RT2-2: an unknown-agent replan is rejected at "
          "merge time; no bad step is spliced into the plan.")
