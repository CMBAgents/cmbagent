"""Unit tests for adaptive-planning merge + routing (no LLM, no execution).

Covers:
  - `merge_plan` freezes the completed prefix and swaps only the tail.
  - `route_after_review` routes replan / advance / halt correctly, including
    halt-on-failure and plan-exhausted.
  - `route_after_merge` advances or ends.

Run:
    python tests/test_adaptive_merge.py
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from langgraph.graph import END

from cmbagent_lg.planning.schemas import Plan, Step
from cmbagent_lg.deep_research.schemas import AdaptiveReview
from cmbagent_lg.deep_research.nodes import (
    merge_plan,
    route_after_review,
    route_after_merge,
)


def _step(name: str) -> Step:
    return Step(sub_task=name, sub_task_agent="engineer", bullet_points=[f"do {name}"])


def _plan(*names) -> Plan:
    return Plan(sub_tasks=[_step(n) for n in names])


def test_merge_freezes_prefix():
    """4-step plan with 2 steps completed → first 2 steps byte-identical, tail
    replaced by the 2-step new_tail.

    `step_index` is run_step's 1-based pointer to the NEXT step to run, so after
    2 completed steps it is 3 (n_done = step_index - 1 = 2). The task's
    "step_index=2 → freeze 2" is that same "2 steps completed" state.
    """
    original = _plan("s1", "s2", "s3", "s4")
    new_tail = _plan("t1", "t2")
    review = AdaptiveReview(needs_adaptation=True, recommendations=["rewrite tail"])

    state = {
        "plan": original,
        "step_index": 3,               # 2 steps completed
        "new_tail": new_tail,
        "adaptive_review": review,
        "adaptive_history": [],
    }
    out = merge_plan(state, runtime=None)
    merged = out["plan"]

    assert len(merged.sub_tasks) == 4, merged
    # Completed prefix byte-identical (same objects, same serialization).
    assert merged.sub_tasks[0].model_dump() == original.sub_tasks[0].model_dump()
    assert merged.sub_tasks[1].model_dump() == original.sub_tasks[1].model_dump()
    # Tail fully replaced by new_tail.
    assert merged.sub_tasks[2].model_dump() == new_tail.sub_tasks[0].model_dump()
    assert merged.sub_tasks[3].model_dump() == new_tail.sub_tasks[1].model_dump()
    # Original tail steps are gone.
    merged_names = [s.sub_task for s in merged.sub_tasks]
    assert "s3" not in merged_names and "s4" not in merged_names, merged_names
    # History records the revision.
    hist = out["adaptive_history"]
    assert len(hist) == 1 and hist[0]["after_step"] == 2
    assert hist[0]["recommendations"] == ["rewrite tail"]
    print("ok test_merge_freezes_prefix")


def test_route_after_review_replan():
    """needs_adaptation=True, step fulfilled, tail remaining → replan_tail."""
    state = {
        "plan": _plan("s1", "s2", "s3"),
        "step_index": 2,                                     # step 1 done
        "step_outcomes": [{"step_number": 1, "fulfilled": True}],
        "adaptive_review": AdaptiveReview(needs_adaptation=True, recommendations=["x"]),
    }
    assert route_after_review(state, runtime=None) == "replan_tail"
    print("ok test_route_after_review_replan")


def test_route_after_review_advance():
    """needs_adaptation=False, step fulfilled, tail remaining → execute_step."""
    state = {
        "plan": _plan("s1", "s2", "s3"),
        "step_index": 2,
        "step_outcomes": [{"step_number": 1, "fulfilled": True}],
        "adaptive_review": AdaptiveReview(needs_adaptation=False),
    }
    assert route_after_review(state, runtime=None) == "run_step"
    print("ok test_route_after_review_advance")


def test_route_after_review_halt_on_failure():
    """A failed step halts the run regardless of the review verdict."""
    state = {
        "plan": _plan("s1", "s2", "s3"),
        "step_index": 2,
        "step_outcomes": [{"step_number": 1, "fulfilled": False, "reason": "boom"}],
        # Even if the reviewer somehow asked to adapt, failure wins.
        "adaptive_review": AdaptiveReview(needs_adaptation=True, recommendations=["x"]),
    }
    assert route_after_review(state, runtime=None) == END
    print("ok test_route_after_review_halt_on_failure")


def test_route_after_review_plan_exhausted():
    """Last step done (pointer past the end) → END, no needless review branch."""
    state = {
        "plan": _plan("s1", "s2"),
        "step_index": 3,                                     # both steps done
        "step_outcomes": [
            {"step_number": 1, "fulfilled": True},
            {"step_number": 2, "fulfilled": True},
        ],
        "adaptive_review": AdaptiveReview(needs_adaptation=True, recommendations=["x"]),
    }
    assert route_after_review(state, runtime=None) == END
    print("ok test_route_after_review_plan_exhausted")


def test_route_after_merge():
    """Advance while steps remain; END once the merged plan is exhausted."""
    remaining = {"plan": _plan("s1", "s2", "s3"), "step_index": 2}
    assert route_after_merge(remaining, runtime=None) == "run_step"

    done = {"plan": _plan("s1", "s2"), "step_index": 3}
    assert route_after_merge(done, runtime=None) == END
    print("ok test_route_after_merge")


if __name__ == "__main__":
    test_merge_freezes_prefix()
    test_route_after_review_replan()
    test_route_after_review_advance()
    test_route_after_review_halt_on_failure()
    test_route_after_review_plan_exhausted()
    test_route_after_merge()
    print("ALL test_adaptive_merge PASSED")
