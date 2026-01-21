"""One-shot execution workflow for CMBAgent.

This module provides a simple, single-task execution workflow where a task
is executed once without iterative planning or human feedback.
"""

import os
import time
import json
import datetime
import requests
from pathlib import Path

# Import from parent package
from ..utils import (
    work_dir_default,
    default_agents_llm_model,
    default_llm_model,
    default_formatter_model,
    get_api_keys_from_env,
    get_model_config,
    camb_context_url,
    classy_context_url,
)
from ..context import shared_context as shared_context_default


def one_shot(
    task,
    max_rounds=50,
    max_n_attempts=3,
    engineer_model=default_agents_llm_model['engineer'],
    researcher_model=default_agents_llm_model['researcher'],
    plot_judge_model=default_agents_llm_model['plot_judge'],
    camb_context_model=default_agents_llm_model['camb_context'],
    default_llm_model_arg=default_llm_model,
    default_formatter_model_arg=default_formatter_model,
    researcher_filename=shared_context_default['researcher_filename'],
    agent='engineer',
    work_dir=work_dir_default,
    api_keys=None,
    clear_work_dir=False,
    evaluate_plots=False,
    max_n_plot_evals=1,
    inject_wrong_plot: bool | str = False,
):
    """Execute a single task using CMBAgent without iterative planning.

    This workflow is designed for straightforward tasks that can be completed
    in one execution without complex planning or human intervention.

    Parameters
    ----------
    task : str
        The task description to execute
    max_rounds : int, optional
        Maximum number of agent interaction rounds, by default 50
    max_n_attempts : int, optional
        Maximum number of code execution attempts, by default 3
    engineer_model : str, optional
        Model to use for the engineer agent
    researcher_model : str, optional
        Model to use for the researcher agent
    plot_judge_model : str, optional
        Model to use for plot evaluation
    camb_context_model : str, optional
        Model to use for CAMB context agent
    default_llm_model_arg : str, optional
        Default LLM model for agents
    default_formatter_model_arg : str, optional
        Default model for response formatters
    researcher_filename : str, optional
        Filename for researcher output
    agent : str, optional
        Initial agent to use ('engineer', 'researcher', 'camb_context', 'classy_context'),
        by default 'engineer'
    work_dir : str or Path, optional
        Working directory for outputs
    api_keys : dict, optional
        API keys for LLM providers. If None, loads from environment
    clear_work_dir : bool, optional
        Whether to clear the working directory before execution, by default False
    evaluate_plots : bool, optional
        Whether to evaluate generated plots with VLM, by default False
    max_n_plot_evals : int, optional
        Maximum number of plot evaluation iterations, by default 1
    inject_wrong_plot : bool or str, optional
        For testing: inject an intentionally wrong plot, by default False

    Returns
    -------
    dict
        Results dictionary containing:
        - chat_history: Full conversation history
        - final_context: Final context variables
        - engineer: Engineer agent object
        - engineer_response_formatter: Engineer formatter object
        - researcher: Researcher agent object
        - researcher_response_formatter: Researcher formatter object
        - plot_judge: Plot judge agent object
        - plot_debugger: Plot debugger agent object
        - initialization_time: Time spent initializing (seconds)
        - execution_time: Time spent executing (seconds)

    Examples
    --------
    >>> from cmbagent.workflows import one_shot
    >>> results = one_shot(
    ...     task="Compute the sum of first 100 natural numbers",
    ...     agent='engineer',
    ...     work_dir='./output'
    ... )
    >>> print(results['execution_time'])
    """
    # Import here to avoid circular dependency
    from ..cmbagent import CMBAgent

    start_time = time.time()
    work_dir = os.path.expanduser(work_dir)
    work_dir = Path(work_dir).expanduser().resolve()

    if api_keys is None:
        api_keys = get_api_keys_from_env()

    engineer_config = get_model_config(engineer_model, api_keys)
    researcher_config = get_model_config(researcher_model, api_keys)
    plot_judge_config = get_model_config(plot_judge_model, api_keys)
    camb_context_config = get_model_config(camb_context_model, api_keys)

    cmbagent = CMBAgent(
        mode="one_shot",
        work_dir=work_dir,
        agent_llm_configs={
            'engineer': engineer_config,
            'researcher': researcher_config,
            'plot_judge': plot_judge_config,
            'camb_context': camb_context_config,
        },
        clear_work_dir=clear_work_dir,
        api_keys=api_keys,
        default_llm_model=default_llm_model_arg,
        default_formatter_model=default_formatter_model_arg,
    )

    end_time = time.time()
    initialization_time = end_time - start_time

    start_time = time.time()

    shared_context = {
        'max_n_attempts': max_n_attempts,
        'evaluate_plots': evaluate_plots,
        'max_n_plot_evals': max_n_plot_evals,
        'inject_wrong_plot': inject_wrong_plot
    }

    if agent == 'camb_context':
        # Fetch the file (30-second safety timeout)
        resp = requests.get(camb_context_url, timeout=30)
        resp.raise_for_status()  # Raises an HTTPError for non-200 codes
        camb_context = resp.text  # Whole document as one long string
        shared_context["camb_context"] = camb_context

    if agent == 'classy_context':
        # Fetch the file (30-second safety timeout)
        resp = requests.get(classy_context_url, timeout=30)
        resp.raise_for_status()  # Raises an HTTPError for non-200 codes
        classy_context = resp.text  # Whole document as one long string
        shared_context["classy_context"] = classy_context

    if researcher_filename is not None:
        shared_context["researcher_filename"] = researcher_filename

    cmbagent.solve(
        task,
        max_rounds=max_rounds,
        initial_agent=agent,
        mode="one_shot",
        shared_context=shared_context
    )

    end_time = time.time()
    execution_time = end_time - start_time

    # Create a dummy groupchat attribute if it doesn't exist
    if not hasattr(cmbagent, 'groupchat'):
        Dummy = type('Dummy', (object,), {'new_conversable_agents': []})
        cmbagent.groupchat = Dummy()

    # Display cost report
    cmbagent.display_cost()

    results = {
        'chat_history': cmbagent.chat_result.chat_history,
        'final_context': cmbagent.final_context,
        'engineer': cmbagent.get_agent_object_from_name('engineer'),
        'engineer_response_formatter': cmbagent.get_agent_object_from_name('engineer_response_formatter'),
        'researcher': cmbagent.get_agent_object_from_name('researcher'),
        'researcher_response_formatter': cmbagent.get_agent_object_from_name('researcher_response_formatter'),
        'plot_judge': cmbagent.get_agent_object_from_name('plot_judge'),
        'plot_debugger': cmbagent.get_agent_object_from_name('plot_debugger')
    }

    results['initialization_time'] = initialization_time
    results['execution_time'] = execution_time

    # Save timing report as JSON
    timing_report = {
        'initialization_time': initialization_time,
        'execution_time': execution_time,
        'total_time': initialization_time + execution_time
    }

    # Add timestamp
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    # Save to JSON file in workdir
    timing_path = os.path.join(work_dir, f"time/timing_report_{timestamp}.json")

    with open(timing_path, 'w') as f:
        json.dump(timing_report, f, indent=2)

    print("\nTiming report saved to", timing_path)
    print("\nTask took", f"{execution_time:.4f}", "seconds")

    # Delete empty folders during control
    database_full_path = os.path.join(results['final_context']['work_dir'], results['final_context']['database_path'])
    codebase_full_path = os.path.join(results['final_context']['work_dir'], results['final_context']['codebase_path'])
    time_full_path = os.path.join(results['final_context']['work_dir'], 'time')

    for folder in [database_full_path, codebase_full_path, time_full_path]:
        if os.path.exists(folder) and not os.listdir(folder):
            os.rmdir(folder)

    return results
