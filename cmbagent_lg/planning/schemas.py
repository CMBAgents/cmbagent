"""Pydantic schemas for the planner / plan_reviewer loop.

Mirrors cmbagent's `PlannerResponse` and `PlanReviewerResponse` so a plan
produced here is shape-compatible with the AG2 implementation.
"""

from typing import List, Optional
from pydantic import BaseModel, Field


class Step(BaseModel):
    """One step of the plan. Equivalent to cmbagent's `Subtasks`."""

    sub_task: str = Field(description="The sub-task to be performed.")
    sub_task_agent: str = Field(
        description="Name of the agent in charge of the sub-task "
        ""
    )
    bullet_points: List[str] = Field(
        description="Bullet-point instructions for the agent carrying out this sub-task."
    )
    code_execution_timeout: Optional[int] = Field(
        default=None,
        description="Max seconds for code execution in this step. None = default.",
    )


class Plan(BaseModel):
    """The structured plan. Equivalent to cmbagent's `PlannerResponse`."""

    sub_tasks: List[Step]

    def format(self) -> str:
        out = ""
        for i, step in enumerate(self.sub_tasks, start=1):
            out += f"\n- Step {i}:\n\t* sub-task: {step.sub_task}\n\t* agent in charge: {step.sub_task_agent}\n"
            if step.code_execution_timeout is not None:
                out += f"\t* code_execution_timeout: {step.code_execution_timeout}s\n"
            if step.bullet_points:
                out += "\t* instructions:\n"
                for b in step.bullet_points:
                    out += f"\t\t- {b}\n"
        return f"**PLAN**\n{out}"


class Review(BaseModel):
    """The structured review. Equivalent to cmbagent's `PlanReviewerResponse`."""

    recommendations: List[str] = Field(
        description="Concrete recommendations to modify the current plan."
    )

    def format(self) -> str:
        out = "**Recommendations:**\n\n"
        for r in self.recommendations:
            out += f"- {r}\n"
        return out
