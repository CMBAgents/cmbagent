"""Operator text must not reach only half the loop.

Measured on the metabric fleet: `params.yaml` put both "no Fine-Gray implementation
exists here, substitute and carry on" and "a negative outcome completes a step" into
`engineer_append_instructions`. The engineer obeyed both. The step evaluator was never
shown either — `step_evaluator_instructions` rendered only
`evaluator_append_instructions` — and refused the engineer's correct substitution eight
times, until the step exhausted its budget and halted the project. The agent doing the
refusing had never been told the facts it was refusing against.

The contract (see `PlanContext.shared_append_instructions`):
  * `shared_append_instructions`    → engineer AND both evaluators, unlabelled. Facts
    about the environment, and about what counts as a finished step.
  * `engineer_append_instructions`  → the engineer, plus the evaluators under an
    explicit "instructions the engineer was given" header — never merged into the
    evaluator's own directions, but never dropped either, so a deployment that already
    states its facts there keeps working.
  * `evaluator_append_instructions` → the evaluators only.

Run:
    python tests/test_append_instructions.py
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cmbagent_lg.context import PlanContext
from cmbagent_lg.planning.schemas import Step
from cmbagent_lg.self_debug.prompts import (
    engineer_append_block,
    engineer_instructions,
    evaluator_append_block,
    evaluator_instructions,
    step_evaluator_instructions,
)

FACT = "scikit-survival 0.28.0 has no Fine-Gray subdistribution regression."
HOWTO = "Prefer pyarrow for parquet I/O."
JUDGING = "Be strict about printed uncertainties."


def _step():
    return Step(sub_task="model recurrence risk", sub_task_agent="engineer",
                bullet_points=["fit Fine-Gray", "print the coefficients"])


def _engineer(ctx):
    return engineer_instructions(ctx, _step(), retry_context="")


def _step_eval(ctx, agent="engineer"):
    return step_evaluator_instructions(
        ctx, Step(sub_task="s", sub_task_agent=agent, bullet_points=["b"]),
        stdout="x", data_manifest=[], step_feedback_history=[])


def _exec_eval(ctx):
    return evaluator_instructions(ctx, _step(), executed_code="print(1)", stdout="x",
                                  stderr="", returncode=0, timed_out=False)


# ── the blocks ────────────────────────────────────────────────────────────────


def test_shared_text_reaches_both_blocks():
    ctx = PlanContext(main_task="t", shared_append_instructions=FACT)
    assert FACT in engineer_append_block(ctx)
    assert FACT in evaluator_append_block(ctx)


def test_engineer_text_reaches_the_evaluator_but_labelled():
    ctx = PlanContext(main_task="t", engineer_append_instructions=HOWTO)
    assert HOWTO in engineer_append_block(ctx)
    block = evaluator_append_block(ctx)
    assert HOWTO in block, "the evaluator must not be blind to what the engineer was told"
    assert "INSTRUCTIONS THE ENGINEER WAS GIVEN" in block, \
        "it must be labelled, not merged into the evaluator's own directions"
    assert "not yours" in block


def test_evaluator_text_stays_out_of_the_engineer_prompt():
    ctx = PlanContext(main_task="t", evaluator_append_instructions=JUDGING)
    assert JUDGING not in engineer_append_block(ctx)
    assert JUDGING in evaluator_append_block(ctx)


def test_no_operator_text_renders_nothing():
    """A run that sets none of the three must have a byte-identical prompt to before
    the shared field existed."""
    ctx = PlanContext(main_task="t")
    assert engineer_append_block(ctx) == ""
    assert evaluator_append_block(ctx) == ""
    ctx2 = PlanContext(main_task="t", shared_append_instructions="   \n  ")
    assert engineer_append_block(ctx2) == "" and evaluator_append_block(ctx2) == ""


def test_ordering_is_shared_then_engineer_then_evaluator():
    ctx = PlanContext(main_task="t", shared_append_instructions=FACT,
                      engineer_append_instructions=HOWTO,
                      evaluator_append_instructions=JUDGING)
    block = evaluator_append_block(ctx)
    assert block.index(FACT) < block.index(HOWTO) < block.index(JUDGING)


# ── the prompts that render them ─────────────────────────────────────────────


def test_the_fleet_shape_now_reaches_the_step_evaluator():
    """The exact metabric mistake: the fact is in the ENGINEER field only."""
    ctx = PlanContext(main_task="t", engineer_append_instructions=FACT)
    assert FACT in _engineer(ctx)
    assert FACT in _step_eval(ctx), "the agent doing the refusing must see the fact"
    assert FACT in _exec_eval(ctx)


def test_the_recommended_shape_reaches_every_agent():
    ctx = PlanContext(main_task="t", shared_append_instructions=FACT)
    for prompt in (_engineer(ctx), _step_eval(ctx), _exec_eval(ctx)):
        assert FACT in prompt


def test_a_researcher_step_evaluator_still_gets_the_operator_text():
    """Gating the engineer-only RULES on the agent must not also gate the operator's
    own text, which is about the run, not about the agent."""
    ctx = PlanContext(main_task="t", shared_append_instructions=FACT)
    prose = _step_eval(ctx, agent="researcher")
    assert FACT in prose
    assert "infeasible_requirements" not in prose, "the channel is still engineer-only"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
