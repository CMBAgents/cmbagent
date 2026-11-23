"""The step-evaluator rule for a step spec that contradicts the data: judge against the data, and declare a requirement
the contradiction rules out infeasible instead of failing the step again.

A DeepSeek step evaluator spent 45-51k characters of reasoning on "four columns, no header" against a three-column
file with a header (b09-2, 23 Nov 2026) and never reached a verdict.

    PYTHONPATH=. python -m pytest tests/test_spec_contradicts_data_rule.py
"""
from cmbagent_lg.context import PlanContext
from cmbagent_lg.planning.schemas import Step
from cmbagent_lg.self_debug.prompts import step_evaluator_instructions


def _prompt(agent):
    step = Step(sub_task="load the table", sub_task_agent=agent,
                bullet_points=["the file has four columns and no header"])
    return " ".join(step_evaluator_instructions(
        PlanContext(main_task="t"), step, stdout="columns: a b c\n", data_manifest=[],
        step_feedback_history=[]).split())


def test_rule_rendered_for_an_engineer_step():
    body = _prompt("engineer")
    assert "A STEP SPEC THAT CONTRADICTS THE DATA" in body
    assert "judge against the data" in body and "infeasible_requirements" in body
    assert "Decide once" in body


def test_rule_not_rendered_for_another_agent():
    assert "CONTRADICTS THE DATA" not in _prompt("researcher")
