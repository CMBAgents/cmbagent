"""Shared context for CMBAgent multi-agent system.

This dictionary stores state shared across all agents during task execution.
It's initialized with defaults and updated during the conversation flow.
"""

shared_context = {
    # =========================================================================
    # Task Information
    # =========================================================================
    "main_task": None,
    "improved_main_task": None,

    # =========================================================================
    # Planning Phase
    # =========================================================================
    "plans": [],
    "reviews": [],
    "proposed_plan": None,
    "recommendations": None,
    "feedback_left": 1,
    "final_plan": None,
    "number_of_steps_in_plan": None,
    "maximum_number_of_steps_in_plan": 5,

    # =========================================================================
    # Execution Control (Control Phase)
    # =========================================================================
    "current_plan_step_number": None,
    "current_sub_task": None,
    "agent_for_sub_task": None,
    "current_status": None,
    "current_instructions": None,
    "previous_steps_execution_summary": "\n",

    # =========================================================================
    # Agent Handoff Flags
    # =========================================================================
    "transfer_to_engineer": False,
    "transfer_to_researcher": False,
    "transfer_to_camb_context": False,

    # =========================================================================
    # File Paths
    # =========================================================================
    "database_path": "data/",
    "codebase_path": "codebase/",

    # =========================================================================
    # Codebase Tracking
    # =========================================================================
    "current_codebase": None,
    "displayed_images": [],

    # =========================================================================
    # Agent-Specific Instructions
    # =========================================================================
    "planner_append_instructions": None,
    "plan_reviewer_append_instructions": None,
    "engineer_append_instructions": None,
    "researcher_append_instructions": None,

    # =========================================================================
    # Retry Control
    # =========================================================================
    "n_attempts": 0,  # Number of failed code execution attempts
    "max_n_attempts": 3,

    # =========================================================================
    # Domain-Specific: AAS Keywords (Astronomy)
    # =========================================================================
    "AAS_keywords_string": None,
    "text_input_for_AAS_keyword_finder": None,
    "N_AAS_keywords": 5,

    # =========================================================================
    # Domain-Specific: CAMB Context (Cosmology)
    # =========================================================================
    "camb_context": None,

    # =========================================================================
    # Other Settings
    # =========================================================================
    "hardware_constraints": None,
    "researcher_filename": "provide a suitable filename given the nature of the notes. Prefer markdown extension unless otherwise instructed.",
}
