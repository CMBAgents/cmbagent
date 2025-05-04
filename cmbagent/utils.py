# cmbagent/utils.py
import os
from openai import OpenAI

from pprint import pprint

from autogen.coding import LocalCommandLineCodeExecutor

import autogen as autogen
from autogen import AssistantAgent
# from autogen.agentchat.contrib.retrieve_user_proxy_agent import RetrieveUserProxyAgent 
from autogen.cmbagent_utils import cmbagent_debug
from autogen import UserProxyAgent, config_list_from_json, GroupChat
from autogen.agentchat.contrib.gpt_assistant_agent import GPTAssistantAgent

from cobaya.yaml import yaml_load_file, yaml_load

from IPython.display import Image

import importlib
import sys
import pickle

import logging
from ruamel.yaml import YAML

# Configure logging
logging.basicConfig(level=logging.INFO, format='[%(name)s] %(message)s')

import autogen
cmbagent_debug = autogen.cmbagent_debug



# Get the path of the current file
path_to_basedir = os.path.dirname(os.path.abspath(__file__))
if cmbagent_debug:
    print('path_to_basedir: ', path_to_basedir)

# Construct the path to the APIs directory
path_to_apis = os.path.join(path_to_basedir, "apis")
if cmbagent_debug:
    print('path_to_apis: ', path_to_apis)

# Construct the path to the assistants directory
path_to_assistants = os.path.join(path_to_basedir, "agents/rag_agents/")
if cmbagent_debug:
    print('path_to_assistants: ', path_to_assistants)
path_to_agents = os.path.join(path_to_basedir, "agents/")

# path_to_engineer = os.path.join(path_to_basedir, "engineer")
# path_to_planner = os.path.join(path_to_basedir, "planner")
# path_to_executor = os.path.join(path_to_basedir, "executor")
# path_to_admin = os.path.join(path_to_basedir, "admin")

if "site-packages" in path_to_basedir or "dist-packages" in path_to_basedir:
    work_dir = os.path.join(os.getcwd(), "cmbagent_output")
    if not os.path.exists(work_dir):
        os.makedirs(work_dir)
else:
    work_dir = os.path.join(path_to_basedir, "../output")

if cmbagent_debug:
    print('\n\n\n\n\nwork_dir: ', work_dir)


default_chunking_strategy = {
    "type": "static",
    "static": {
        "max_chunk_size_tokens": 200, # reduce size to ensure better context integrity
        "chunk_overlap_tokens": 100 # increase overlap to maintain context across chunks
    }
}

# notes from https://platform.openai.com/docs/assistants/tools/file-search:

# max_chunk_size_tokens must be between 100 and 4096 inclusive.
# chunk_overlap_tokens must be non-negative and should not exceed max_chunk_size_tokens / 2.

# By default, the file_search tool outputs up to 20 chunks for gpt-4* models and up to 5 chunks for gpt-3.5-turbo. 
# You can adjust this by setting file_search.max_num_results in the tool when creating the assistant or the run.

default_top_p = 0.05
default_temperature = 0.00001


default_select_speaker_prompt_template = """
Read the above conversation. Then select the next role from {agentlist} to play. Only return the role.
Note that only planner can modify or update the PLAN. planner should not be selected after the PLAN has been approved.
executor should not be selected unless admin says "execute".
engineer should be selected to check for conflicts. 
engineer should be selected to check code. 
engineer should be selected to provide code to save summary of session. 
executor should be selected to execute. 
planner should be the first agent to speak. 
"""
### note that we hardcoded the requirement that planner speaks first. 


default_select_speaker_message_template = """
You are in a role play game about cosmological data analysis. The following roles are available:
                {roles}.
                Read the following conversation.
                Then select the next role from {agentlist} to play. Only return the role.
Note that only planner can modify or update the PLAN.
planner should not be selected after the PLAN has been approved.
executor should not be selected unless admin says "execute".
engineer should be selected to check for conflicts. 
engineer should be selected to check code. 
executor should be selected to execute. 
planner should be the first agent to speak.
"""


default_groupchat_intro_message = """
We have assembled a team of LLM agents and a human admin to solve Cosmological data analysis tasks. 

In attendance are:
"""

# TODO
# see https://github.com/openai/openai-python/blob/da48e4cac78d1d4ac749e2aa5cfd619fde1e6c68/src/openai/types/beta/file_search_tool.py#L20
# default_file_search_max_num_results = 20
# The default is 20 for `gpt-4*` models and 5 for `gpt-3.5-turbo`. This number
# should be between 1 and 50 inclusive.
file_search_max_num_results = autogen.file_search_max_num_results

default_max_round = 50

default_llm_model = 'gpt-4o-2024-11-20'
# default_llm_model = 'gpt-4.1-2025-04-14'
# default_llm_model = 'gpt-4o-mini'
# "gpt-4o-mini"

default_llm_config_list = [
                    {
                        "model": default_llm_model,
                        "api_key": os.getenv("OPENAI_API_KEY"),
                        'api_type': 'openai',
                    }
                    ]

default_agent_llm_configs = {
                    'engineer': {
                        "model": "o3-mini-2025-01-31",
                        "reasoning_effort": "medium", # high
                        "api_key": os.getenv("OPENAI_API_KEY"),
                        "api_type": "openai",
                        },
                    'aas_keyword_finder': {
                        "model": "o3-mini-2025-01-31",
                        # "model": default_llm_model,
                        "reasoning_effort": "medium", # high
                        "api_key": os.getenv("OPENAI_API_KEY"),
                        "api_type": "openai",
                        },
                    'classy_sz': {
                        # "model": "gpt-4o-2024-11-20", 
                        "model": default_llm_model,
                        "api_key": os.getenv("OPENAI_API_KEY"), # use oai models here only (this is a RAG agent, with an oai vector store attached. If you are not a cosmologist, ignore this, you will never need this agent.
                        "api_type": "openai",
                        },
                    'camb': {
                        # "model": "gpt-4o-2024-11-20",
                        "model": default_llm_model,
                        "api_key": os.getenv("OPENAI_API_KEY"),
                        # "check_every_ms": 500, # does not do anything
                        "api_type": "openai",
                        },
                    'cobaya': {
                        "model": "gpt-4o-2024-11-20",
                        "api_key": os.getenv("OPENAI_API_KEY"),
                        "api_type": "openai",
                        },
                    'task_improver': {
                        "model": "o3-mini-2025-01-31",
                        "reasoning_effort": "high",
                        "api_key": os.getenv("OPENAI_API_KEY"),
                        "api_type": "openai",
                        },
                    'task_recorder': {
                        "model": "gpt-4o-2024-11-20",
                        "api_key": os.getenv("OPENAI_API_KEY"),
                        "api_type": "openai",
                        },
                    'planner': {
                        "model": default_llm_model,#"gpt-4o-2024-11-20",
                        "api_key": os.getenv("OPENAI_API_KEY"),
                        "api_type": "openai",
                        },
                    'control': {
                        # "model": "gpt-4o-2024-11-20",
                        "model": default_llm_model,
                        # "model": "o3-mini-2025-01-31",
                        # "reasoning_effort": "medium",
                        "api_key": os.getenv("OPENAI_API_KEY"),
                        "api_type": "openai",
                        },
                    'terminator': {
                        "model": "gpt-4o-2024-11-20",
                        # "model": "o3-mini-2025-01-31",
                        # "reasoning_effort": "high",
                        "api_key": os.getenv("OPENAI_API_KEY"),
                        "api_type": "openai",
                        },

                    'researcher': {
                        "model": "gemini-2.5-pro-exp-03-25",
                        "api_key": os.getenv("GEMINI_API_KEY"),
                        "api_type": "google",
                        },

                    'perplexity': {
                        "model": "o3-mini-2025-01-31",
                        "reasoning_effort": "medium", # high
                        "api_key": os.getenv("OPENAI_API_KEY"),
                        "api_type": "openai",
                        },

                    'plan_reviewer': {
                        "model": "claude-3-7-sonnet-20250219",
                        "api_key": os.getenv("ANTHROPIC_API_KEY"),
                        "api_type": "anthropic",
                        },


                    'idea_hater': {
                        "model": "claude-3-7-sonnet-20250219",
                        "api_key": os.getenv("ANTHROPIC_API_KEY"),
                        "api_type": "anthropic",
                        },

                    # 'idea_maker': {
                    #     "model": "gemini-2.5-pro-exp-03-25",
                    #     "api_key": os.getenv("GEMINI_API_KEY"),
                    #     "api_type": "google",
                    #     },
                    "idea_maker": {
                        "model": default_llm_model,
                        "api_key": os.getenv("OPENAI_API_KEY"),
                        "api_type": "openai",
                        'temperature': 0.99,
                        },
                    # 'idea_maker': {
                    #     "model": "gemini-2.5-pro-exp-03-25",
                    #     "api_key": os.getenv("GEMINI_API_KEY"),
                    #     "api_type": "google",
                    #     },
                    "classy_sz_response_formatter": {
                        "model": default_llm_model,
                        "api_key": os.getenv("OPENAI_API_KEY"),
                        "api_type": "openai",
                        },
                    "camb_response_formatter": {
                        # "model": "gpt-4o-2024-11-20", 
                        "model": default_llm_model,
                        "api_key": os.getenv("OPENAI_API_KEY"),
                        "api_type": "openai",
                        },
                    "cobaya_response_formatter": {
                        "model": "gpt-4o-2024-11-20",
                        "api_key": os.getenv("OPENAI_API_KEY"),
                        "api_type": "openai",
                        },
                    # "engineer_response_formatter": {
                    #     "model": "o3-mini-2025-01-31",
                    #     "reasoning_effort": "low",
                    #     "api_key": os.getenv("OPENAI_API_KEY"),
                    #     "api_type": "openai",
                    #     },
                    "engineer_response_formatter": {
                        "model": "o3-mini-2025-01-31",
                        "reasoning_effort": "medium",
                        "api_key": os.getenv("OPENAI_API_KEY"),
                        "api_type": "openai",
                        },

                    "researcher_response_formatter": {
                        "model": "o3-mini-2025-01-31",
                        "reasoning_effort": "low",
                        "api_key": os.getenv("OPENAI_API_KEY"),
                        "api_type": "openai",
                        },

                    }



def update_yaml_preserving_format(yaml_file, agent_name, new_id, field = 'vector_store_ids'):
    yaml = YAML()
    yaml.preserve_quotes = True  # This preserves quotes in the YAML file if they are present

    # Load the YAML file while preserving formatting
    with open(yaml_file, 'r') as file:
        yaml_content = yaml.load(file)
    
    # Update the vector_store_id for the specific agent
    if yaml_content['name'] == agent_name:
        if field == 'vector_store_ids':
            yaml_content['assistant_config']['tool_resources']['file_search']['vector_store_ids'][0] = new_id
        elif field == 'assistant_id':
            yaml_content['assistant_config']['assistant_id'] = new_id
    else:
        print(f"Agent {agent_name} not found.")
    
    # Write the changes back to the YAML file while preserving formatting
    with open(yaml_file, 'w') as file:
        yaml.dump(yaml_content, file)

def aas_keyword_to_url(keyword):
    """
    Given an AAS keyword, return its IAU Thesaurus URL.
    
    Args:
        keyword (str): The AAS keyword (e.g., "H II regions")
        
    Returns:
        str: The corresponding IAU Thesaurus URL
    """
    with open('aas_kwd_to_url.pkl', 'rb') as f:
        dic = pickle.load(f)
    return dic[keyword]


with open(path_to_basedir + '/aas_kwd_to_url.pkl', 'rb') as file:
    AAS_keywords_dict = pickle.load(file)

# print(my_dict)
# Assuming you have already loaded your dictionary into `my_dict`
AAS_keywords_string = ', '.join(AAS_keywords_dict.keys())

