from autogen import SwarmResult
from typing import Literal

def register_functions_to_agents(cmbagent_instance):
    '''
    This function registers the functions to the agents.
    '''
    planner = cmbagent_instance.get_agent_from_name('planner')
    planner_response_formatter = cmbagent_instance.get_agent_from_name('planner_response_formatter')
    plan_recorder = cmbagent_instance.get_agent_from_name('plan_recorder')
    plan_reviewer = cmbagent_instance.get_agent_from_name('plan_reviewer')
    reviewer_response_formatter = cmbagent_instance.get_agent_from_name('reviewer_response_formatter')
    review_recorder = cmbagent_instance.get_agent_from_name('review_recorder')
    researcher = cmbagent_instance.get_agent_from_name('researcher')
    researcher_response_formatter = cmbagent_instance.get_agent_from_name('researcher_response_formatter')
    engineer = cmbagent_instance.get_agent_from_name('engineer')
    engineer_response_formatter = cmbagent_instance.get_agent_from_name('engineer_response_formatter')
    classy_sz = cmbagent_instance.get_agent_from_name('classy_sz_agent')
    classy_sz_response_formatter = cmbagent_instance.get_agent_from_name('classy_sz_response_formatter')
    executor = cmbagent_instance.get_agent_from_name('executor')
    plan_implementer = cmbagent_instance.get_agent_from_name('plan_implementer')
    admin = cmbagent_instance.get_agent_from_name('admin')




    def record_plan(plan_suggestion: str, number_of_steps_in_plan: int, context_variables: dict) -> SwarmResult:
        """
        Records a suggested plan and updates relevant execution context.

        This function logs a full plan suggestion into the `context_variables` dictionary. If no feedback 
        remains to be given (i.e., `context_variables["feedback_left"] == 0`), the most recent plan 
        suggestion is marked as the final plan. The function also updates the total number of steps in 
        the plan.

        The function ensures that the plan is properly stored and transferred to the `plan_reviewer` agent 
        for further evaluation.

        Args:
            plan_suggestion (str): The complete plan suggestion to be recorded.
            number_of_steps_in_plan (int): The total number of **Steps** in the suggested plan.
            context_variables (dict): A dictionary maintaining execution context, including previous plans, 
                feedback tracking, and finalized plans.
        """
        context_variables["plans"].append(plan_suggestion)

        context_variables["number_of_steps_in_plan"] = number_of_steps_in_plan

        if context_variables["feedback_left"]==0:
            context_variables["final_plan"] = context_variables["plans"][-1]
            return SwarmResult(agent=plan_implementer, ## transfer to plan implementer
                            values="Final plan has been logged. Starting implementation.",
                            context_variables=context_variables)
        else:
            return SwarmResult(agent=plan_reviewer, ## transfer to plan reviewer
                            values="Plan has been logged.",
                            context_variables=context_variables)


    plan_recorder._add_single_function(record_plan)



    def record_review(plan_review: str, context_variables: dict) -> SwarmResult:
        """ Record reviews of the plan."""
        context_variables["reviews"].append(plan_review)
        context_variables["feedback_left"] -= 1

        # if context_variables["feedback_left"]


        # Controlling the flow to the next agent from a tool call
        # if context_variables["reviews_left"] < 0:
        #     context_variables["plan_recorded"] = True
        #     return SwarmResult(agent=plan_manager,
        #                        values="No further recommendations to be made on the plan. Update plan and proceed",
        #                        context_variables=context_variables)
        # else:
        return SwarmResult(agent=planner,  ## transfer back to planner
                        values=f"""
Recommendations have been logged.  
Number of feedback rounds left: {context_variables["feedback_left"]}. 
Now, update the plan accordingly, planner!""",
                        
                        context_variables=context_variables)


    review_recorder._add_single_function(record_review)


    def record_status(
        current_status: Literal["in progress", "failed", "completed"],
        current_plan_step_number: int,
        current_sub_task: str,
        current_instructions: str,
        agent_for_sub_task: str,
        context_variables: dict
    ) -> SwarmResult:
        """
        Updates the execution context and returns the current progress.
        Must be called before and after each action taken.

        Args:
            current_status (str): The current status ("in progress", "failed", or "completed").
            current_plan_step_number (int): The current step number in the plan.
            current_sub_task (str): Description of the current sub-task.
            current_instructions (str): Instructions for the sub-task.
            agent_for_sub_task (str): The agent responsible for the sub-task.
            context_variables (dict): Execution context dictionary.

        Returns:
            SwarmResult: Contains a formatted status message and updated context.
        """
        context_variables["current_plan_step_number"] = current_plan_step_number
        context_variables["current_sub_task"] = current_sub_task
        context_variables["agent_for_sub_task"] = agent_for_sub_task
        context_variables["current_instructions"] = current_instructions
        context_variables["current_status"] = current_status

        #     return SwarmResult(
        #                        values=f"""
        # **Step number:** {context_variables["current_plan_step_number"]} out of {context_variables["number_of_steps_in_plan"]}.\n 
        # **Status:** {context_variables["current_status"]}\n 
        # **Sub-task:** {context_variables["current_sub_task"]}\n 
        # **Agent in charge of sub-task:** `{context_variables["agent_for_sub_task"]}`\n 
        # **Instructions:**\n 
        # {context_variables["current_instructions"]}
        # """,
        #                        context_variables=context_variables)


        # Map statuses to icons
        status_icons = {
            "completed": "✅",
            "failed": "❌",
            "in progress": "⏳"  # or any other icon you prefer
        }
        
        icon = status_icons.get(current_status, "")
        
        context_variables["current_plan_step_number"] = current_plan_step_number
        context_variables["current_sub_task"] = current_sub_task
        context_variables["agent_for_sub_task"] = agent_for_sub_task
        context_variables["current_instructions"] = current_instructions
        context_variables["current_status"] = current_status

        return SwarmResult(
            values=f"""
**Step number:** {context_variables["current_plan_step_number"]} out of {context_variables["number_of_steps_in_plan"]}.\n 
**Sub-task:** {context_variables["current_sub_task"]}\n 
**Agent in charge of sub-task:** `{context_variables["agent_for_sub_task"]}`\n 
**Instructions:**\n 
{context_variables["current_instructions"]}\n 
**Status:** {context_variables["current_status"]} {icon}
    """,
            context_variables=context_variables)
    
    plan_implementer._add_single_function(record_status)
