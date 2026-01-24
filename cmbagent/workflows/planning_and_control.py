"""Planning and control workflow for CMBAgent.

.. deprecated::
    This workflow is deprecated and will be removed in a future version.
    Use :func:`deep_research` instead, which provides the same functionality
    with improved context carryover and better step tracking.

This workflow implements a two-phase approach with separate planning and control
phases. It first creates a plan, then executes it in a single control phase.

Note: This workflow does NOT support context carryover between plan steps.
For multi-step execution with context carryover, use deep_research instead.
"""

import os
import json
import time
import copy
import datetime
import warnings
from pathlib import Path

from ..agents.planning.planner_response_formatter.planner_response_formatter import save_final_plan
from ..utils import (
    work_dir_default,
    default_llm_model as default_llm_model_default,
    default_formatter_model as default_formatter_model_default,
    default_agents_llm_model,
    get_model_config,
    get_api_keys_from_env
)
from ..context import shared_context as shared_context_default


def planning_and_control(
    task,
    max_rounds_planning=50,
    max_rounds_control=100,
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
    researcher_filename=shared_context_default['researcher_filename'],
    default_llm_model=default_llm_model_default,
    default_formatter_model=default_formatter_model_default,
    api_keys=None,
):
    """Execute a task with planning and control phases (DEPRECATED).

    .. deprecated::
        This function is deprecated and will be removed in a future version.
        Use :func:`deep_research` instead for improved multi-step execution
        with context carryover between steps.

    **Limitations:**
    - No context carryover between plan steps
    - Single control phase executes entire plan at once
    - Less suitable for complex multi-step workflows

    **Recommended Alternative:**
    Use :func:`deep_research` which provides:
    - Context carryover between steps
    - Step-by-step execution with summaries
    - Better support for long-running research tasks
    - Ability to restart from specific steps

    Parameters
    ----------
    task : str
        The research task description
    max_rounds_planning : int, optional
        Maximum rounds for planning phase, by default 50
    max_rounds_control : int, optional
        Maximum rounds for control phase, by default 100
    max_plan_steps : int, optional
        Maximum number of steps in the plan, by default 3
    n_plan_reviews : int, optional
        Number of plan review iterations, by default 1
    plan_instructions : str, optional
        Additional instructions for the planner
    engineer_instructions : str, optional
        Additional instructions for the engineer agent
    researcher_instructions : str, optional
        Additional instructions for the researcher agent
    hardware_constraints : str, optional
        Hardware constraints to consider
    max_n_attempts : int, optional
        Maximum number of retry attempts, by default 3
    planner_model : str, optional
        Model to use for planner agent
    plan_reviewer_model : str, optional
        Model to use for plan reviewer agent
    engineer_model : str, optional
        Model to use for engineer agent
    researcher_model : str, optional
        Model to use for researcher agent
    idea_maker_model : str, optional
        Model to use for idea maker agent
    idea_hater_model : str, optional
        Model to use for idea hater agent
    work_dir : str, optional
        Working directory for outputs
    researcher_filename : str, optional
        Filename for researcher output
    default_llm_model : str, optional
        Default LLM model for unspecified agents
    default_formatter_model : str, optional
        Default model for response formatters
    api_keys : dict, optional
        API keys for model providers

    Returns
    -------
    dict
        Results dictionary containing:
        - chat_history: List of all conversation messages
        - final_context: Final execution context
        - initialization_time_planning: Time spent on planning initialization
        - execution_time_planning: Time spent on planning execution
        - initialization_time_control: Time spent on control initialization
        - execution_time_control: Time spent on control execution

    Warnings
    --------
    DeprecationWarning
        This function is deprecated. Use deep_research instead.

    See Also
    --------
    deep_research : Recommended replacement with context carryover
    """
    # Import here to avoid circular dependency
    from ..cmbagent import CMBAgent

    # Issue deprecation warning
    warnings.warn(
        "planning_and_control is deprecated and will be removed in a future version. "
        "Use deep_research instead for improved multi-step execution with context carryover.",
        DeprecationWarning,
        stacklevel=2
    )

    # Create work directory if it doesn't exist
    Path(work_dir).expanduser().resolve().mkdir(parents=True, exist_ok=True)

    ## planning
    planning_dir = Path(work_dir).expanduser().resolve() / "planning"
    planning_dir.mkdir(parents=True, exist_ok=True)

    start_time = time.time()

    if api_keys is None:
        api_keys = get_api_keys_from_env()

    planner_config = get_model_config(planner_model, api_keys)
    plan_reviewer_config = get_model_config(plan_reviewer_model, api_keys)

    cmbagent = CMBAgent(
        work_dir=planning_dir,
        default_llm_model=default_llm_model,
        default_formatter_model=default_formatter_model,
        agent_llm_configs={
            'planner': planner_config,
            'plan_reviewer': plan_reviewer_config,
        },
        api_keys=api_keys
    )
    end_time = time.time()
    initialization_time_planning = end_time - start_time

    start_time = time.time()
    cmbagent.solve(
        task,
        max_rounds=max_rounds_planning,
        initial_agent="plan_setter",
        shared_context={
            'feedback_left': n_plan_reviews,
            'max_n_attempts': max_n_attempts,
            'maximum_number_of_steps_in_plan': max_plan_steps,
            'planner_append_instructions': plan_instructions,
            'engineer_append_instructions': engineer_instructions,
            'researcher_append_instructions': researcher_instructions,
            'plan_reviewer_append_instructions': plan_instructions,
            'hardware_constraints': hardware_constraints,
            'researcher_filename': researcher_filename
        }
    )
    end_time = time.time()
    execution_time_planning = end_time - start_time

    # Create a dummy groupchat attribute if it doesn't exist
    if not hasattr(cmbagent, 'groupchat'):
        Dummy = type('Dummy', (object,), {'new_conversable_agents': []})
        cmbagent.groupchat = Dummy()

    # Now call display_cost without triggering the AttributeError
    cmbagent.display_cost()

    planning_output = copy.deepcopy(cmbagent.final_context)
    outfile = save_final_plan(planning_output, planning_dir)
    print(f"Structured plan written to {outfile}")
    print(f"Planning took {execution_time_planning:.4f} seconds")

    # Save timing report as JSON
    timing_report = {
        'initialization_time_planning': initialization_time_planning,
        'execution_time_planning': execution_time_planning,
        'total_time': initialization_time_planning + execution_time_planning
    }

    # Add timestamp
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    timing_path = os.path.join(planning_output['work_dir'], f"time/timing_report_planning_{timestamp}.json")
    with open(timing_path, 'w') as f:
        json.dump(timing_report, f, indent=2)

    print(f"\nTiming report data saved to: {timing_path}\n")

    ## delete empty folders during control
    database_full_path = os.path.join(planning_output['work_dir'], planning_output['database_path'])
    codebase_full_path = os.path.join(planning_output['work_dir'], planning_output['codebase_path'])
    time_full_path = os.path.join(planning_output['work_dir'], 'time')
    for folder in [database_full_path, codebase_full_path, time_full_path]:
        if os.path.exists(folder) and not os.listdir(folder):
            os.rmdir(folder)

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
        default_llm_model=default_llm_model,
        default_formatter_model=default_formatter_model,
        agent_llm_configs={
            'engineer': engineer_config,
            'researcher': researcher_config,
            'idea_maker': idea_maker_config,
            'idea_hater': idea_hater_config,
        },
        api_keys=api_keys
    )

    end_time = time.time()
    initialization_time_control = end_time - start_time

    start_time = time.time()
    cmbagent.solve(
        task,
        max_rounds=max_rounds_control,
        initial_agent="controller",
        shared_context=planning_output
    )
    end_time = time.time()
    execution_time_control = end_time - start_time

    results = {
        'chat_history': cmbagent.chat_result.chat_history,
        'final_context': cmbagent.final_context
    }

    results['initialization_time_planning'] = initialization_time_planning
    results['execution_time_planning'] = execution_time_planning
    results['initialization_time_control'] = initialization_time_control
    results['execution_time_control'] = execution_time_control

    # Save timing report as JSON
    timing_report = {
        'initialization_time_planning': initialization_time_planning,
        'execution_time_planning': execution_time_planning,
        'initialization_time_control': initialization_time_control,
        'execution_time_control': execution_time_control,
        'total_time': initialization_time_planning + execution_time_planning + initialization_time_control + execution_time_control
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
        if os.path.exists(folder) and not os.listdir(folder):
            os.rmdir(folder)

    return results
