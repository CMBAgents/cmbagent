from .utils import AAS_keywords_string
shared_context = {

    "plans": [],
    "reviews": [],
    "proposed_plan": None,
    "recommendations": None,
    "feedback_left": 1,

    "number_of_steps_in_plan": None,
    "maximum_number_of_steps_in_plan": 5,
    "final_plan": None,
    "current_plan_step_number": None,
    "current_sub_task": None,
    "agent_for_sub_task": None,
    "current_status": None,
    "current_instructions": None,


    "main_task": None,
    "improved_main_task": None,
    "database_path": "data/",
    "codebase_path": "codebase/",

    "current_codebase": None,
    "displayed_images": [],

    "transfer_to_engineer": False,
    "transfer_to_researcher": False,
    "transfer_to_camb_agent": False,
    "transfer_to_cobaya_agent": False,
    "transfer_to_perplexity": False,

    "planner_append_instructions": None,
    "plan_reviewer_append_instructions": None,
    "engineer_append_instructions": None,
    "researcher_append_instructions": None,


    "AAS_keywords_string": AAS_keywords_string,
    "text_input_for_AAS_keyword_finder": None,
    "N_AAS_keywords": 5,

    "perplexity_query": None,
    "perplexity_response": None,
    "perplexity_citations": None,


    "n_attempts": 0,
    "max_n_attempts": 3,


}
