"""RT2 attack 4 — MALFORMED / refusing reviewer is now FAIL-SAFE.

Original vuln: `adaptive_review` called
    chat_model(...).with_structured_output(AdaptiveReview).invoke(...)
with NO try/except, so a provider error / refusal / pydantic ValidationError
propagated and CRASHED the whole compiled graph. A None result was safe only by
luck (it happened to land on the advance path).

Mitigation (V7): the `.invoke(...)` is wrapped in try/except; on ANY exception OR
a None result the node falls back to
    AdaptiveReview(needs_adaptation=False, recommendations=[],
                   reason="review failed — fail-safe: continue")
so a model/parse failure NEVER aborts the run.

We monkeypatch `nodes.chat_model` so NO API key is needed; the REAL
`adaptive_review` node runs against the fake client.

Run: python tests/adv_rt2_malformed_review.py
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cmbagent_lg.context import PlanContext
from cmbagent_lg.planning.schemas import Plan, Step
from cmbagent_lg.deep_research import nodes as adaptive_nodes
from cmbagent_lg.deep_research.nodes import adaptive_review, route_after_review


class _Runtime:
    def __init__(self, ctx):
        self.context = ctx


def _step(name):
    return Step(sub_task=name, sub_task_agent="engineer", bullet_points=[f"do {name}"])


def _state():
    return {
        "plan": Plan(sub_tasks=[_step("s1"), _step("s2")]),
        "step_index": 2,                       # step 1 done
        "step_summaries": ["did s1"],
        "step_outcomes": [{"step_number": 1, "fulfilled": True}],
    }


# Formatter pattern (R-02): `adaptive_review` first calls the critic reasoning
# model for PROSE (`chat_model(...).invoke`), THEN the FORMATTER
# (`_formatter(ctx).with_structured_output(AdaptiveReview).invoke`) structures it.
# The V7 fail-safe wraps the FORMATTER call. So here the reasoning succeeds (a
# fake prose message) and the FORMATTER misbehaves (raises / returns None).
class _FakeStructured:
    def __init__(self, behavior):
        self._behavior = behavior

    def invoke(self, *a, **k):
        if self._behavior == "raise":
            # e.g. pydantic ValidationError: reviewer omitted `needs_adaptation`
            raise ValueError("1 validation error for AdaptiveReview: "
                             "needs_adaptation field required")
        if self._behavior == "none":
            return None
        raise AssertionError(self._behavior)


class _FakeFormatter:
    """Stand-in for `_formatter(ctx)`: its structured output misbehaves."""
    def __init__(self, behavior):
        self._behavior = behavior

    def with_structured_output(self, schema):
        return _FakeStructured(self._behavior)


class _FakeMsg:
    text = "The remaining steps look fine; no change needed."


class _FakeReasoner:
    """Stand-in for the critic reasoning model: prose succeeds."""
    def invoke(self, *a, **k):
        return _FakeMsg()


def _patch(behavior):
    """Reasoning succeeds (prose); the FORMATTER then raises/None → V7 fail-safe."""
    saved = (adaptive_nodes.chat_model, adaptive_nodes._formatter)
    adaptive_nodes.chat_model = lambda *a, **k: _FakeReasoner()
    adaptive_nodes._formatter = lambda *a, **k: _FakeFormatter(behavior)
    return saved


def _unpatch(saved):
    adaptive_nodes.chat_model, adaptive_nodes._formatter = saved


# ── (a) formatter raises -> node returns a fail-safe verdict, no crash ─────
def test_a_malformed_review_is_failsafe():
    saved = _patch("raise")
    try:
        out = adaptive_review(_state(), _Runtime(PlanContext(main_task="rt2")))
    finally:
        _unpatch(saved)
    review = out["adaptive_review"]
    # FIXED: no exception escaped; a safe "do not adapt" verdict is returned.
    assert review is not None
    assert review.needs_adaptation is False, review
    assert review.recommendations == []
    # And routing continues the run (advance), never crashes.
    route = route_after_review({**_state(), **out}, runtime=None)
    assert route == "run_step", route
    print("ok  (a) malformed review (formatter raises) -> fail-safe "
          "needs_adaptation=False; run advances, no crash")


# ── (b) formatter returns None -> same fail-safe verdict ──────────────────
def test_b_none_review_is_failsafe():
    saved = _patch("none")
    try:
        out = adaptive_review(_state(), _Runtime(PlanContext(main_task="rt2")))
    finally:
        _unpatch(saved)
    review = out["adaptive_review"]
    # FIXED: None is normalized to a concrete fail-safe verdict (not left as None).
    assert review is not None, "None result must be normalized to a fail-safe review"
    assert review.needs_adaptation is False, review
    route = route_after_review({**_state(), **out}, runtime=None)
    assert route == "run_step", route
    print("ok  (b) None review -> normalized to fail-safe needs_adaptation=False; "
          "run advances")


if __name__ == "__main__":
    test_a_malformed_review_is_failsafe()
    test_b_none_review_is_failsafe()
    print("MITIGATION VERIFIED RT2-4: an unparseable/None reviewer output is a "
          "fail-safe no-adaptation verdict; the run never crashes.")
