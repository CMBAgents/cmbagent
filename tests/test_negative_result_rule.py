"""A negative result completes the step.

metabric Project1/Iteration2 step 5 had the bullet "The Love plot must demonstrate that
post-weighting SMDs are below 0.1". The engineer fitted the IPTW model correctly and
measured the balance: 17 of 20 SMDs fell below 0.1, and TMB_NONSYNONYMOUS (0.2518),
GRADE (0.1036) and AGE_AT_DIAGNOSIS (0.1034) did not. The step evaluator refused that
eight times, because the bullet reads as a demand for a NUMBER rather than for a
measurement — and no propensity model can be ordered to produce a prescribed value. The
step burned its whole budget and halted the project over a correct analysis.

The rule renders into every engineer step-evaluator prompt: a sub-task can require work
to be done and reported, never that the answer come out a particular way, so an honest
unwelcome value fulfils the requirement. It deliberately does NOT soften the
data-integrity rules — a silent run, fabricated data, the wrong column or a fit that did
not converge has measured nothing, so there is no negative result to report.

Run:
    python tests/test_negative_result_rule.py
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cmbagent_lg.context import PlanContext
from cmbagent_lg.planning.schemas import Step
from cmbagent_lg.self_debug.prompts import STEP_EVALUATOR_YAML, step_evaluator_instructions


def _prompt(agent="engineer", **ctx_kw):
    ctx = PlanContext(main_task="t", **ctx_kw)
    step = Step(sub_task="assess covariate balance after IPTW", sub_task_agent=agent,
                bullet_points=["The Love plot must demonstrate that post-weighting "
                               "SMDs are below 0.1."])
    return step_evaluator_instructions(
        ctx, step, stdout="TMB_NONSYNONYMOUS 0.2518\n", data_manifest=[],
        step_feedback_history=[])


def test_the_rule_is_rendered_for_an_engineer_step():
    body = " ".join(_prompt().lower().split())
    # The pairing the replay harness measures: a negative-result phrase AND a
    # completion phrase, both in the instruction text.
    assert any(n in body for n in (
        "negative result", "negative outcome", "negative finding", "null result",
        "unachieved criterion"))
    assert any(n in body for n in (
        "complete", "finish", "does not fail", "fulfil", "fulfill"))


def test_the_rule_names_the_shapes_that_misled_the_evaluator():
    body = " ".join(_prompt().lower().split())
    # A bullet phrased as an outcome is a requirement to MEASURE.
    assert "measure" in body
    # The fleet's own shapes.
    for needle in ("threshold not reached", "not significant", "cut-off"):
        assert needle in body, needle
    # And the instruction not to spend another attempt hoping for a better number.
    assert "another attempt" in body


def test_the_rule_does_not_soften_the_data_integrity_rules():
    """Otherwise it is a blanket excuse: "the analysis found nothing" would pass a run
    that never analysed anything."""
    # Collapsed, so an assertion is about the prose and not about where it wraps.
    block = " ".join(STEP_EVALUATOR_YAML["negative_result_rules"].lower().split())
    assert "does not soften the data-integrity rules" in block
    for needle in ("printed nothing", "fabricated", "wrong column", "did not converge"):
        assert needle in block, needle
    assert "has not measured anything" in block
    # The data-integrity rules themselves are still in the prompt beside it.
    body = " ".join(_prompt().lower().split())
    assert "silent run" in body and "fit did not converge" in body


def test_the_rule_is_engineer_only():
    """The researcher's evaluator has its own template; this one is the engineer's."""
    assert "negative result" not in _prompt(agent="researcher").lower()


def test_safe_loading_off_keeps_the_rule():
    """`safe_loading=False` empties the data-integrity rules; the negative-result rule
    is about what a requirement MEANS, not about data provenance, so it stays."""
    body = " ".join(_prompt(safe_loading=False).lower().split())
    assert "negative result" in body
    assert "silent run" not in body, "the data-integrity rules are gated as before"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
