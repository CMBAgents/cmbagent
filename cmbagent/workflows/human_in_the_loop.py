"""Human-in-the-loop workflow for CMBAgent.

This workflow provides an interactive mode where the user can provide feedback
and guidance during task execution. It's designed for tasks that benefit from
human oversight and iterative refinement.
"""

import os
import json
import time
import datetime

from ..utils import (
    work_dir_default,
    get_model_config,
    get_api_keys_from_env
)


def human_in_the_loop(
    task,
    work_dir=work_dir_default,
    max_rounds=50,
    max_n_attempts=3,
    engineer_model='gpt-4o-2024-11-20',
    researcher_model='gpt-4o-2024-11-20',
    agent='engineer',
    api_keys=None,
):
    """Execute a task with human-in-the-loop interaction.

    This workflow allows for interactive execution where the user can provide
    feedback, corrections, and guidance at each step. It's particularly useful
    for exploratory tasks or when iterative refinement is needed.

    Parameters
    ----------
    task : str
        The task description to execute
    work_dir : str, optional
        Working directory for outputs, by default work_dir_default
    max_rounds : int, optional
        Maximum number of interaction rounds, by default 50
    max_n_attempts : int, optional
        Maximum number of retry attempts, by default 3
    engineer_model : str, optional
        Model to use for engineer agent, by default 'gpt-4o-2024-11-20'
    researcher_model : str, optional
        Model to use for researcher agent, by default 'gpt-4o-2024-11-20'
    agent : str, optional
        Initial agent to start with ('engineer' or 'researcher'), by default 'engineer'
    api_keys : dict, optional
        API keys for model providers, by default None

    Returns
    -------
    dict
        Results dictionary containing:
        - chat_history: List of all conversation messages
        - final_context: Final execution context
        - engineer: Engineer agent object
        - engineer_nest: Engineer nest agent object
        - engineer_response_formatter: Engineer formatter object
        - researcher: Researcher agent object
        - researcher_response_formatter: Researcher formatter object
        - initialization_time: Time spent on initialization (seconds)
        - execution_time: Time spent on execution (seconds)

    Examples
    --------
    >>> from cmbagent.workflows import human_in_the_loop
    >>> results = human_in_the_loop(
    ...     task="Explore climate modeling approaches",
    ...     agent='researcher',
    ...     work_dir='./output'
    ... )
    >>> print(results['execution_time'])
    """
    # Import here to avoid circular dependency
    from ..cmbagent import CMBAgent

    ## control
    start_time = time.time()

    if api_keys is None:
        api_keys = get_api_keys_from_env()

    engineer_config = get_model_config(engineer_model, api_keys)
    researcher_config = get_model_config(researcher_model, api_keys)

    cmbagent = CMBAgent(
        work_dir=work_dir,
        agent_llm_configs={
            'engineer': engineer_config,
            'researcher': researcher_config,
        },
        mode="human_in_the_loop",
        chat_agent=agent,
        api_keys=api_keys
    )

    end_time = time.time()
    initialization_time = end_time - start_time

    start_time = time.time()

    cmbagent.solve(
        task,
        max_rounds=max_rounds,
        initial_agent=agent,
        shared_context={'max_n_attempts': max_n_attempts},
        mode="human_in_the_loop"
    )

    end_time = time.time()
    execution_time = end_time - start_time

    results = {
        'chat_history': cmbagent.chat_result.chat_history,
        'final_context': cmbagent.final_context,
        'engineer': cmbagent.get_agent_object_from_name('engineer'),
        'engineer_nest': cmbagent.get_agent_object_from_name('engineer_nest'),
        'engineer_response_formatter': cmbagent.get_agent_object_from_name('engineer_response_formatter'),
        'researcher': cmbagent.get_agent_object_from_name('researcher'),
        'researcher_response_formatter': cmbagent.get_agent_object_from_name('researcher_response_formatter')
    }

    results['initialization_time'] = initialization_time
    results['execution_time'] = execution_time

    if not hasattr(cmbagent, 'groupchat'):
        Dummy = type('Dummy', (object,), {'new_conversable_agents': []})
        cmbagent.groupchat = Dummy()

    # Now call display_cost without triggering the AttributeError
    cmbagent.display_cost()

    # Save timing report as JSON
    timing_report = {
        'initialization_time': initialization_time,
        'execution_time': execution_time,
        'total_time': initialization_time + execution_time
    }

    # Add timestamp
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    # Save to JSON file in workdir
    timing_path = os.path.join(work_dir, f"timing_report_{timestamp}.json")
    with open(timing_path, 'w') as f:
        json.dump(timing_report, f, indent=2)

    return results
