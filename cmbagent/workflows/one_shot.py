"""One-shot execution workflow for CMBAgent.

This module provides a simple, single-task execution workflow where a task
is executed once without iterative planning or human feedback.
"""

import os
import time
import json
import datetime
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
)
from ..context import shared_context as shared_context_default
from ..utils.context_utils import add_contexts_from_urls


def one_shot(
    task,
    max_rounds=50,
    max_n_attempts=3,
    engineer_model=default_agents_llm_model['engineer'],
    researcher_model=default_agents_llm_model['researcher'],
    camb_context_model=default_agents_llm_model['camb_context'],
    default_llm_model_arg=default_llm_model,
    default_formatter_model_arg=default_formatter_model,
    researcher_filename=shared_context_default['researcher_filename'],
    agent='engineer',
    work_dir=work_dir_default,
    api_keys=None,
    clear_work_dir=False,
    use_massgen=False,
    massgen_config=None,
    massgen_verbose=False,
    massgen_enable_logging=True,
    massgen_use_for_retries=False,
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
    use_massgen : bool, optional
        Use MassGen multi-agent system for engineer code generation, by default False
    massgen_config : str, optional
        Path to MassGen YAML configuration. If None, uses default config.
    massgen_verbose : bool, optional
        Enable verbose output for MassGen, by default False
    massgen_enable_logging : bool, optional
        Enable detailed logging for MassGen, by default True
    massgen_use_for_retries : bool, optional
        Use MassGen for retry attempts (debugging). If False (default), uses single LLM
        for faster debugging after initial code generation.

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
    camb_context_config = get_model_config(camb_context_model, api_keys)

    cmbagent = CMBAgent(
        mode="one_shot",
        work_dir=work_dir,
        agent_llm_configs={
            'engineer': engineer_config,
            'researcher': researcher_config,
            'camb_context': camb_context_config,
        },
        clear_work_dir=clear_work_dir,
        api_keys=api_keys,
        default_llm_model=default_llm_model_arg,
        default_formatter_model=default_formatter_model_arg,
        use_massgen=use_massgen,
        massgen_config=massgen_config,
        massgen_verbose=massgen_verbose,
        massgen_enable_logging=massgen_enable_logging,
        massgen_use_for_retries=massgen_use_for_retries,
    )

    end_time = time.time()
    initialization_time = end_time - start_time

    start_time = time.time()

    shared_context = {
        'max_n_attempts': max_n_attempts,
    }

    # Fetch context documentation if needed for specific agents
    context_urls_map = {
        'camb_context': camb_context_url,
    }

    if agent in context_urls_map:
        # Fetch context documentation with 30-second timeout
        add_contexts_from_urls(
            shared_context,
            {agent: context_urls_map[agent]},
            timeout=30
        )

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
        'researcher_response_formatter': cmbagent.get_agent_object_from_name('researcher_response_formatter')
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
