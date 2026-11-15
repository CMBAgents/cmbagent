"""Planning module prompts — loads `planner.yaml` and `plan_reviewer.yaml`,
renders them against a `PlanContext` via `str.format_map(SafeDict(...))`.
Missing placeholders render as empty strings (templates don't break mid-loop).
"""

from cmbagent_lg.context import PlanContext
from cmbagent_lg.prompt_utils import SafeDict, load_yaml


PLANNER_YAML = load_yaml("cmbagent_lg.planning.templates", "planner.yaml")
PLAN_REVIEWER_YAML = load_yaml("cmbagent_lg.planning.templates", "plan_reviewer.yaml")


def planner_instructions(ctx: PlanContext, recommendations: str) -> str:
    return PLANNER_YAML["instructions"].format_map(
        SafeDict(
            main_task=ctx.main_task,
            planner_append_instructions=ctx.planner_append_instructions,
            hardware_constraints=ctx.hardware_constraints,
            code_execution_timeout=ctx.code_execution_timeout,
            maximum_number_of_steps_in_plan=ctx.maximum_number_of_steps_in_plan,
            recommendations=recommendations or "(none yet — this is the first plan)",
        )
    )


def plan_reviewer_instructions(ctx: PlanContext, proposed_plan: str) -> str:
    return PLAN_REVIEWER_YAML["instructions"].format_map(
        SafeDict(
            main_task=ctx.main_task,
            plan_reviewer_append_instructions=ctx.plan_reviewer_append_instructions,
            hardware_constraints=ctx.hardware_constraints,
            code_execution_timeout=ctx.code_execution_timeout,
            maximum_number_of_steps_in_plan=ctx.maximum_number_of_steps_in_plan,
            proposed_plan=proposed_plan,
        )
    )
