"""RT2 attack 6 — OVERSIZED / garbage Step fields downstream.

Probes whether a degenerate Step (huge bullet list, empty sub_task, `None`
timeout, unicode/None-ish junk) crashes the parts of the pipeline that touch
step fields without an LLM: `Plan.format()`, `merge_plan`, the routers, and
deep_research's `_build_step_summary`.

Finding: NO crash — these paths iterate/format defensively and `None` timeout is
in fact the documented default. Reported here as a NEGATIVE result (what did NOT
break) so the red-team picture is complete.

Run: python tests/adv_rt2_oversized_fields.py
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cmbagent_lg.planning.schemas import Plan, Step
from cmbagent_lg.deep_research import schemas as adaptive_schemas
from cmbagent_lg.deep_research.nodes import merge_plan, route_after_merge
from cmbagent_lg.deep_research.nodes import _build_step_summary


def test_oversized_step_does_not_crash():
    huge = Step(
        sub_task="",                                   # empty task
        sub_task_agent="engineer",
        bullet_points=[f"instruction {i} " + "x" * 200 for i in range(10_000)],
        code_execution_timeout=None,                   # None = default (fine)
    )
    plan = Plan(sub_tasks=[Step(sub_task="s1", sub_task_agent="engineer",
                                bullet_points=["ok"]), huge])

    # Plan.format over a 10k-bullet step
    s = plan.format()
    assert "Step 2" in s

    # merge_plan with the oversized step as the new tail
    state = {
        "plan": Plan(sub_tasks=[Step(sub_task="s1", sub_task_agent="engineer",
                                     bullet_points=["ok"]),
                                Step(sub_task="s2", sub_task_agent="engineer",
                                     bullet_points=["ok"])]),
        "step_index": 2,
        "new_tail": Plan(sub_tasks=[huge]),
        "adaptive_review": adaptive_schemas.AdaptiveReview(needs_adaptation=True,
                                                           recommendations=["x"]),
        "adaptive_history": [],
    }
    merged = merge_plan(state, runtime=None)["plan"]
    assert len(merged.sub_tasks[1].bullet_points) == 10_000
    assert route_after_merge({"plan": merged, "step_index": 2}, runtime=None) == "run_step"

    # _build_step_summary over a degenerate engineer sub_state (no code/stdout)
    summ = _build_step_summary(huge, {"step_number": 2})
    assert "Step 2" in summ

    print("ok  oversized bullets / empty sub_task / None timeout: NO crash "
          "in format/merge/route/summary (negative result)")


if __name__ == "__main__":
    test_oversized_step_does_not_crash()
    print("RT2-6: no downstream crash from oversized/garbage step fields (as expected).")
