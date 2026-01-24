"""Main registration function for CMBAgent functions."""

from autogen import register_function

from .execution_control import create_post_execution_transfer, create_terminate_session
from .planning import create_record_plan, create_record_plan_constraints, create_record_review
from .ideas import create_record_ideas
from .keywords import create_record_aas_keywords
from .status import create_record_status, create_record_status_starter


def register_functions_to_agents(cmbagent_instance):
    """
    This function registers the functions to the agents.
    """
    # Get all agent references
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
    executor = cmbagent_instance.get_agent_from_name('executor')
    executor_response_formatter = cmbagent_instance.get_agent_from_name('executor_response_formatter')
    terminator = cmbagent_instance.get_agent_from_name('terminator')
    controller = cmbagent_instance.get_agent_from_name('controller')
    admin = cmbagent_instance.get_agent_from_name('admin')
    aas_keyword_finder = cmbagent_instance.get_agent_from_name('aas_keyword_finder')
    plan_setter = cmbagent_instance.get_agent_from_name('plan_setter')
    idea_maker = cmbagent_instance.get_agent_from_name('idea_maker')
    installer = cmbagent_instance.get_agent_from_name('installer')
    idea_saver = cmbagent_instance.get_agent_from_name('idea_saver')
    control_starter = cmbagent_instance.get_agent_from_name('control_starter')
    camb_context = cmbagent_instance.get_agent_from_name('camb_context')

    # Create and register execution control functions
    post_execution_transfer = create_post_execution_transfer(
        controller, engineer, camb_context, installer, terminator
    )

    register_function(
        post_execution_transfer,
        caller=executor_response_formatter,
        executor=executor_response_formatter,
        description=r"""
Transfer to the next agent based on the execution status.
For the next agent suggestion, follow these rules:

    - Suggest the installer agent if error related to missing Python modules (i.e., ModuleNotFoundError).
    - Suggest the camb_context agent if CAMB documentation should be consulted, e.g., if the Python error is related to the camb code.
    - Suggest camb_context to fix Python errors related to the camb code.
    - Suggest the engineer agent if error related to generic Python code. Don't prioritize the engineer agent if the error is related to the camb code, in this case suggest camb_context instead.
    - Suggest the controller only if execution was successful.
""",
    )

    terminate_session = create_terminate_session()
    terminator._add_single_function(terminate_session)

    # Create and register planning functions
    record_plan = create_record_plan(plan_reviewer, terminator)

    register_function(
        record_plan,
        caller=plan_recorder,
        executor=plan_recorder,
        description=r"""
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
        """,
    )

    record_plan_constraints = create_record_plan_constraints(cmbagent_instance, planner)
    plan_setter._add_single_function(record_plan_constraints)

    record_review = create_record_review(planner)

    register_function(
        record_review,
        caller=review_recorder,
        executor=review_recorder,
        description=r"""
        Records the reviews of the plan.
        """,
    )

    # Create and register recording functions
    record_ideas = create_record_ideas(cmbagent_instance)

    register_function(
        record_ideas,
        caller=idea_saver,
        executor=idea_saver,
        description=r"""
        Records the ideas. You must record the entire list of ideas and their descriptions. You must not alter the list.
        """,
    )

    record_aas_keywords = create_record_aas_keywords(aas_keyword_finder, controller)

    register_function(
        record_aas_keywords,
        caller=aas_keyword_finder,
        executor=aas_keyword_finder,
        description=r"""
        Extracts the relevant AAS keywords from the list, given the text input.
        Args:
            aas_keywords (list[str]): The list of AAS keywords to be recorded
            context_variables (dict): A dictionary maintaining execution context, including previous plans,
                feedback tracking, and finalized plans.
        """,
    )

    # Create and register status tracking functions
    record_status = create_record_status(cmbagent_instance, controller)

    register_function(
        record_status,
        caller=controller,
        executor=controller,
        description=r"""
        Updates the context and returns the current progress.
        Must be called **before calling the agent in charge of the next sub-task**.
        Must be called **after** each action taken.

        Args:
            current_status (str): The current status ("in progress", "failed", or "completed").
            current_plan_step_number (int): The current step number in the plan.
            current_sub_task (str): Description of the current sub-task.
            current_instructions (str): Instructions for the sub-task.
            agent_for_sub_task (str): The agent responsible for the sub-task.
            context_variables (dict): context dictionary.

        Returns:
            ReplyResult: Contains a formatted status message and updated context.
        """,
    )

    record_status_starter = create_record_status_starter(cmbagent_instance)

    register_function(
        record_status_starter,
        caller=control_starter,
        executor=control_starter,
        description=r"""
        Updates the context and returns the current progress.
        Must be called **before calling the agent in charge of the next sub-task**.
        Must be called **after** each action taken.

        Args:

            context_variables (dict): context dictionary.

        Returns:
            ReplyResult: Contains a formatted status message and updated context.
        """,
    )
