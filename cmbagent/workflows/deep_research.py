"""Deep research workflow for CMBAgent.

This workflow implements a multi-step research process with planning and iterative
execution phases. It creates a plan first, then executes each step with full context
carryover between steps.
"""

import os
import json
import time
import copy
import datetime
import pickle
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


def load_context(context_path):
    """Load context from a pickle file.

    Parameters
    ----------
    context_path : str
        Path to the pickle file containing the context

    Returns
    -------
    dict
        The loaded context dictionary
    """
    with open(context_path, 'rb') as f:
        context = pickle.load(f)
    return context


def clean_work_dir(work_dir):
    """Clean the work directory by removing all files and subdirectories.

    Parameters
    ----------
    work_dir : str
        Path to the work directory to clean
    """
    import shutil
    if os.path.exists(work_dir):
        shutil.rmtree(work_dir)


def deep_research(
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
    camb_context_model=default_agents_llm_model['camb_context'],
    default_llm_model=default_llm_model_default,
    default_formatter_model=default_formatter_model_default,
    work_dir=work_dir_default,
    api_keys=None,
    restart_at_step=-1,
    clear_work_dir=False,
    researcher_filename=shared_context_default['researcher_filename'],
):
    """Execute a complex research task with planning and multi-step execution.

    This workflow first creates a detailed plan, then executes each step iteratively
    with full context carryover. It's designed for complex tasks that require
    multiple phases of work.

    Parameters
    ----------
    task : str
        The research task description
    max_rounds_planning : int, optional
        Maximum rounds for planning phase, by default 50
    max_rounds_control : int, optional
        Maximum rounds for each control step, by default 100
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
        Maximum number of retry attempts per step, by default 3
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
    camb_context_model : str, optional
        Model to use for CAMB context agent
    default_llm_model : str, optional
        Default LLM model for unspecified agents
    default_formatter_model : str, optional
        Default model for response formatters
    work_dir : str, optional
        Working directory for outputs
    api_keys : dict, optional
        API keys for model providers
    restart_at_step : int, optional
        Step number to restart from (-1 or 0 for no restart), by default -1
    clear_work_dir : bool, optional
        Whether to clear work directory before starting, by default False
    researcher_filename : str, optional
        Filename for researcher output

    Returns
    -------
    dict
        Results dictionary containing:
        - chat_history: List of all conversation messages
        - final_context: Final execution context after all steps
        - initialization_time_control: Time spent on initialization
        - execution_time_control: Time spent on execution
    """
    # Import here to avoid circular dependency
    from ..cmbagent import CMBAgent

    # Create work directory if it doesn't exist
    Path(work_dir).expanduser().resolve().mkdir(parents=True, exist_ok=True)
    work_dir = os.path.expanduser(work_dir)

    if clear_work_dir:
        clean_work_dir(work_dir)

    context_dir = Path(work_dir).expanduser().resolve() / "context"
    os.makedirs(context_dir, exist_ok=True)

    print("Created context directory: ", context_dir)

    if api_keys is None:
        api_keys = get_api_keys_from_env()

    ## planning
    if restart_at_step <= 0:

        ## planning
        planning_dir = Path(work_dir).expanduser().resolve() / "planning"
        planning_dir.mkdir(parents=True, exist_ok=True)

        start_time = time.time()

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
        print(f"\nStructured plan written to {outfile}")
        print(f"\nPlanning took {execution_time_planning:.4f} seconds\n")

        context_path = os.path.join(context_dir, "context_step_0.pkl")
        with open(context_path, 'wb') as f:
            pickle.dump(cmbagent.final_context, f)

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

        ## delete empty folders during planning
        database_full_path = os.path.join(planning_output['work_dir'], planning_output['database_path'])
        codebase_full_path = os.path.join(planning_output['work_dir'], planning_output['codebase_path'])
        for folder in [database_full_path, codebase_full_path]:
            if os.path.exists(folder) and not os.listdir(folder):
                os.rmdir(folder)

    ## control
    engineer_config = get_model_config(engineer_model, api_keys)
    researcher_config = get_model_config(researcher_model, api_keys)
    camb_context_config = get_model_config(camb_context_model, api_keys)
    idea_maker_config = get_model_config(idea_maker_model, api_keys)
    idea_hater_config = get_model_config(idea_hater_model, api_keys)

    control_dir = Path(work_dir).expanduser().resolve() / "control"
    control_dir.mkdir(parents=True, exist_ok=True)

    current_context = copy.deepcopy(planning_output) if restart_at_step <= 0 else load_context(os.path.join(context_dir, f"context_step_{restart_at_step-1}.pkl"))
    number_of_steps_in_plan = current_context['number_of_steps_in_plan']
    step_summaries = []

    initial_step = 1 if restart_at_step <= 0 else restart_at_step

    def load_plan(plan_path):
        """Load plan from JSON file."""
        plan_path = os.path.expanduser(plan_path)
        with open(plan_path, 'r') as f:
            plan_dict = json.load(f)
        return plan_dict

    for step in range(initial_step, number_of_steps_in_plan + 1):
        clear_work_dir_step = True if step == 1 and restart_at_step <= 0 else False
        starter_agent = "controller" if step == 1 else "control_starter"

        start_time = time.time()
        cmbagent = CMBAgent(
            work_dir=control_dir,
            clear_work_dir=clear_work_dir_step,
            default_llm_model=default_llm_model,
            default_formatter_model=default_formatter_model,
            agent_llm_configs={
                'engineer': engineer_config,
                'researcher': researcher_config,
                'idea_maker': idea_maker_config,
                'idea_hater': idea_hater_config,
                'camb_context': camb_context_config,
            },
            mode="deep_research",
            api_keys=api_keys
        )

        end_time = time.time()
        initialization_time_control = end_time - start_time

        if step == 1:
            plan_input = load_plan(os.path.join(work_dir, "planning/final_plan.json"))["sub_tasks"]
            agent_for_step = plan_input[0]['sub_task_agent']
        else:
            agent_for_step = current_context['agent_for_sub_task']

        parsed_context = copy.deepcopy(current_context)
        parsed_context["agent_for_sub_task"] = agent_for_step
        parsed_context["current_plan_step_number"] = step
        parsed_context["n_attempts"] = 0  # reset number of failures for each step

        start_time = time.time()

        cmbagent.solve(
            task,
            max_rounds=max_rounds_control,
            initial_agent=starter_agent,
            shared_context=parsed_context,
            step=step
        )

        end_time = time.time()
        execution_time_control = end_time - start_time

        # number of failures:
        number_of_failures = cmbagent.final_context['n_attempts']

        results = {
            'chat_history': cmbagent.chat_result.chat_history,
            'final_context': cmbagent.final_context
        }

        if number_of_failures >= cmbagent.final_context['max_n_attempts']:
            print(f"in deep_research: number of failures: {number_of_failures} >= max_n_attempts: {cmbagent.final_context['max_n_attempts']}. Exiting.")
            break

        # Collect step summaries
        for msg in results['chat_history'][::-1]:
            if 'name' in msg:
                agent_for_step = agent_for_step.removesuffix("_context")
                agent_for_step = agent_for_step.removesuffix("_agent")
                if msg['name'] == agent_for_step or msg['name'] == f"{agent_for_step}_nest" or msg['name'] == f"{agent_for_step}_response_formatter":
                    this_step_execution_summary = msg['content']
                    summary = f"### Step {step}\n{this_step_execution_summary.strip()}"
                    step_summaries.append(summary)
                    cmbagent.final_context['previous_steps_execution_summary'] = "\n\n".join(step_summaries)
                    break

        print("previous_steps_execution_summary: ", cmbagent.final_context['previous_steps_execution_summary'])

        current_context = copy.deepcopy(cmbagent.final_context)

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
        timing_path = os.path.join(current_context['work_dir'], f"time/timing_report_step_{step}_{timestamp}.json")
        with open(timing_path, 'w') as f:
            json.dump(timing_report, f, indent=2)

        print(f"\nTiming report data saved to: {timing_path}\n")

        # Create a dummy groupchat attribute if it doesn't exist
        if not hasattr(cmbagent, 'groupchat'):
            Dummy = type('Dummy', (object,), {'new_conversable_agents': []})
            cmbagent.groupchat = Dummy()

        # Now call display_cost without triggering the AttributeError
        cmbagent.display_cost(name_append=f"step_{step}")

        ## save the chat history and the final context
        chat_full_path = os.path.join(current_context['work_dir'], "chats")
        os.makedirs(chat_full_path, exist_ok=True)
        chat_output_path = os.path.join(chat_full_path, f"chat_history_step_{step}.json")
        with open(chat_output_path, 'w') as f:
            json.dump(results['chat_history'], f, indent=2)

        context_path = os.path.join(context_dir, f"context_step_{step}.pkl")
        with open(context_path, 'wb') as f:
            pickle.dump(cmbagent.final_context, f)

    ## delete empty folders after execution
    database_full_path = os.path.join(current_context['work_dir'], current_context['database_path'])
    codebase_full_path = os.path.join(current_context['work_dir'], current_context['codebase_path'])
    for folder in [database_full_path, codebase_full_path]:
        if os.path.exists(folder) and not os.listdir(folder):
            os.rmdir(folder)

    return results
