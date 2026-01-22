"""Control workflow for CMBAgent.

This workflow executes a pre-existing plan from a JSON file. It loads
a structured plan and executes it directly using the control agent,
without the planning phase.

This is useful when you have a pre-defined plan and want to execute it
directly without re-planning.
"""

import os
import json
import time
import datetime
from pathlib import Path

from ..utils import (
    work_dir_default,
    default_agents_llm_model,
    get_model_config,
    get_api_keys_from_env
)


def load_plan(plan_path):
    """Load a plan from a JSON file into a dictionary.

    Parameters
    ----------
    plan_path : str
        Path to the JSON file containing the plan

    Returns
    -------
    dict
        Dictionary containing the loaded plan structure

    Examples
    --------
    >>> from cmbagent.workflows import load_plan
    >>> plan = load_plan('plans/idea_plan.json')
    >>> print(plan['sub_tasks'])
    """
    plan_path = os.path.expanduser(plan_path)  # Expands '~'
    with open(plan_path, 'r') as f:
        plan_dict = json.load(f)

    return plan_dict


def control(
    task,
    plan=os.path.join(os.path.dirname(os.path.dirname(__file__)), 'plans', 'idea_plan.json'),
    max_rounds=100,
    max_plan_steps=3,
    n_plan_reviews=1,
    plan_instructions='',
    engineer_instructions='',
    researcher_instructions='',
    hardware_constraints='',
    max_n_attempts=3,
    planner_model=default_agents_llm_model['planner'],
    plan_reviewer_model=default_agents_llm_model['plan_reviewer'],
    engineer_model=default_agents_llm_model['engineer'],
    researcher_model=default_agents_llm_model['researcher'],
    idea_maker_model=default_agents_llm_model['idea_maker'],
    idea_hater_model=default_agents_llm_model['idea_hater'],
    work_dir=work_dir_default,
    clear_work_dir=True,
    api_keys=None,
):
    """Execute a task using a pre-existing plan from a JSON file.

    This workflow loads a structured plan from a JSON file and executes it
    directly using the control agent. It skips the planning phase and goes
    straight to execution, making it useful for executing pre-defined plans
    or reusing previously created plans.

    Parameters
    ----------
    task : str
        The task description (used for context)
    plan : str, optional
        Path to JSON file containing the plan, by default uses 'plans/idea_plan.json'
    max_rounds : int, optional
        Maximum rounds for control execution, by default 100
    max_plan_steps : int, optional
        Maximum number of steps in the plan, by default 3
    n_plan_reviews : int, optional
        Number of plan review iterations (not used in control-only), by default 1
    plan_instructions : str, optional
        Additional instructions for the planner (not used in control-only)
    engineer_instructions : str, optional
        Additional instructions for the engineer agent
    researcher_instructions : str, optional
        Additional instructions for the researcher agent
    hardware_constraints : str, optional
        Hardware constraints to consider
    max_n_attempts : int, optional
        Maximum number of retry attempts, by default 3
    planner_model : str, optional
        Model to use for planner agent (not used in control-only)
    plan_reviewer_model : str, optional
        Model to use for plan reviewer agent (not used in control-only)
    engineer_model : str, optional
        Model to use for engineer agent
    researcher_model : str, optional
        Model to use for researcher agent
    idea_maker_model : str, optional
        Model to use for idea maker agent
    idea_hater_model : str, optional
        Model to use for idea hater agent
    work_dir : str, optional
        Working directory for outputs, by default work_dir_default
    clear_work_dir : bool, optional
        Whether to clear the work directory before execution, by default True
    api_keys : dict, optional
        API keys for model providers, by default fetched from environment

    Returns
    -------
    dict
        Results dictionary containing:
        - chat_history: List of all conversation messages
        - final_context: Final execution context
        - initialization_time_control: Time spent on initialization
        - execution_time_control: Time spent on execution

    Examples
    --------
    >>> from cmbagent.workflows import control
    >>> results = control(
    ...     task="Implement a new cosmological model",
    ...     plan="plans/my_custom_plan.json",
    ...     work_dir="./my_project"
    ... )
    >>> print(results['final_context'])

    Notes
    -----
    The plan JSON file should have the following structure:
    {
        "sub_tasks": [
            {
                "sub_task": "Task description",
                "sub_task_agent": "engineer",
                "bullet_points": ["Detail 1", "Detail 2"]
            }
        ]
    }

    See Also
    --------
    deep_research : For workflows that include both planning and execution
    planning_and_control : Legacy workflow with separate planning phase
    """
    # Import here to avoid circular dependency
    from ..cmbagent import CMBAgent

    # check work_dir exists
    if not os.path.exists(work_dir):
        os.makedirs(work_dir)

    planning_input = load_plan(plan)["sub_tasks"]

    context = {'final_plan': planning_input,
               "number_of_steps_in_plan": len(planning_input),
               "agent_for_sub_task": planning_input[0]['sub_task_agent'],
               "current_sub_task": planning_input[0]['sub_task'],
               "current_instructions": ''}
    for bullet in planning_input[0]['bullet_points']:
        context["current_instructions"] += f"\t\t- {bullet}\n"

    if api_keys is None:
        api_keys = get_api_keys_from_env()

    ## control
    engineer_config = get_model_config(engineer_model, api_keys)
    researcher_config = get_model_config(researcher_model, api_keys)
    idea_maker_config = get_model_config(idea_maker_model, api_keys)
    idea_hater_config = get_model_config(idea_hater_model, api_keys)
    control_dir = Path(work_dir).expanduser().resolve() / "control"
    control_dir.mkdir(parents=True, exist_ok=True)

    start_time = time.time()
    cmbagent = CMBAgent(
        work_dir=control_dir,
        agent_llm_configs={
            'engineer': engineer_config,
            'researcher': researcher_config,
            'idea_maker': idea_maker_config,
            'idea_hater': idea_hater_config,
        },
        clear_work_dir=clear_work_dir,
        api_keys=api_keys
    )

    end_time = time.time()
    initialization_time_control = end_time - start_time

    start_time = time.time()
    cmbagent.solve(task,
                   max_rounds=max_rounds,
                   initial_agent="controller",
                   shared_context=context
                   )
    end_time = time.time()
    execution_time_control = end_time - start_time

    results = {'chat_history': cmbagent.chat_result.chat_history,
               'final_context': cmbagent.final_context}

    results['initialization_time_control'] = initialization_time_control
    results['execution_time_control'] = execution_time_control

    # Save timing report as JSON
    timing_report = {
        'initialization_time_control': initialization_time_control,
        'execution_time_control': execution_time_control,
        'total_time': initialization_time_control + execution_time_control
    }

    # Add timestamp
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    # Save to JSON file in workdir
    timing_path = os.path.join(results['final_context']['work_dir'], f"time/timing_report_control_{timestamp}.json")
    with open(timing_path, 'w') as f:
        json.dump(timing_report, f, indent=2)

    # Create a dummy groupchat attribute if it doesn't exist
    if not hasattr(cmbagent, 'groupchat'):
        Dummy = type('Dummy', (object,), {'new_conversable_agents': []})
        cmbagent.groupchat = Dummy()

    # Now call display_cost without triggering the AttributeError
    cmbagent.display_cost()
    ## delete empty folders during control
    database_full_path = os.path.join(results['final_context']['work_dir'], results['final_context']['database_path'])
    codebase_full_path = os.path.join(results['final_context']['work_dir'], results['final_context']['codebase_path'])
    time_full_path = os.path.join(results['final_context']['work_dir'], 'time')
    for folder in [database_full_path, codebase_full_path, time_full_path]:
        if not os.listdir(folder):
            os.rmdir(folder)

    return results
