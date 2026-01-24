"""Functions for planning workflow management."""

from typing import Literal, List
from autogen.agentchat.group import ContextVariables
from autogen.agentchat.group import AgentTarget, ReplyResult


def create_record_plan(plan_reviewer, terminator):
    """Factory function to create record_plan with agent references."""

    def record_plan(
        plan_suggestion: str,
        number_of_steps_in_plan: int,
        context_variables: ContextVariables
    ) -> ReplyResult:
        """
        Records a suggested plan and updates relevant execution context.

        This function logs a full plan suggestion into the `context_variables` dictionary. If no feedback
        remains to be given (i.e., `context_variables["feedback_left"] == 0`), the most recent plan
        suggestion is marked as the final plan. The function also updates the total number of steps in
        the plan.

        The function ensures that the plan is properly stored and transferred to the `plan_reviewer` agent
        for further evaluation.

        Args:
            plan_suggestion (str): The complete plan suggestion to be recorded. Unaltered, as it is, preserve capitalization and ponctuation.
            number_of_steps_in_plan (int): The total number of **Steps** in the suggested plan, which you read off from the plan suggestion.
            context_variables (dict): A dictionary maintaining execution context, including previous plans,
                feedback tracking, and finalized plans.
        """
        context_variables["plans"].append(plan_suggestion)
        context_variables["proposed_plan"] = plan_suggestion
        context_variables["number_of_steps_in_plan"] = number_of_steps_in_plan

        if context_variables["feedback_left"] == 0:
            context_variables["final_plan"] = context_variables["plans"][-1]
            return ReplyResult(
                target=AgentTarget(terminator),
                message="Planning stage complete. Exiting.",
                context_variables=context_variables
            )
        else:
            return ReplyResult(
                target=AgentTarget(plan_reviewer),
                message="Plan has been logged.",
                context_variables=context_variables
            )

    return record_plan


def create_record_plan_constraints(cmbagent_instance, planner):
    """Factory function to create record_plan_constraints with cmbagent instance."""

    def record_plan_constraints(
        needed_agents: List[Literal["engineer", "researcher", "idea_maker", "idea_hater",
                                    "camb_context", "classy_context", "aas_keyword_finder"]],
        context_variables: ContextVariables
    ) -> ReplyResult:
        """Records the constraints on the plan."""

        context_variables["needed_agents"] = needed_agents

        str_to_append = f"The plan must strictly involve only the following agents: {', '.join(needed_agents)}\n"

        str_to_append += r"""
**AGENT ROLES**
Here are the descriptions of the agents that are needed to carry out the plan:
"""
        for agent in set(needed_agents):
            agent_object = cmbagent_instance.get_agent_from_name(agent)
            str_to_append += f'- {agent}: {agent_object.description}'

        str_to_append += "\n"
        str_to_append += r"""
You must not invoke any other agent than the ones listed above.
"""
        context_variables["planner_append_instructions"] += str_to_append
        context_variables["plan_reviewer_append_instructions"] += str_to_append

        return ReplyResult(
            target=AgentTarget(planner),
            message="Plan constraints have been logged.",
            context_variables=context_variables
        )

    return record_plan_constraints


def create_record_review(planner):
    """Factory function to create record_review with agent references."""

    def record_review(plan_review: str, context_variables: ContextVariables) -> ReplyResult:
        """Record reviews of the plan."""
        context_variables["reviews"].append(plan_review)
        context_variables["feedback_left"] -= 1
        context_variables["recommendations"] = plan_review

        return ReplyResult(
            target=AgentTarget(planner),
            message=f"""
Recommendations have been logged.
Number of feedback rounds left: {context_variables["feedback_left"]}.
Now, update the plan accordingly, planner!""",
            context_variables=context_variables
        )

    return record_review
