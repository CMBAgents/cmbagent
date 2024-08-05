# cmbagent/utils.py
import os
from openai import OpenAI

from pprint import pprint

from autogen.coding import LocalCommandLineCodeExecutor

import autogen
from autogen import AssistantAgent
from autogen.agentchat.contrib.retrieve_user_proxy_agent import RetrieveUserProxyAgent

from autogen import UserProxyAgent, config_list_from_json
from autogen.agentchat.contrib.gpt_assistant_agent import GPTAssistantAgent

from cobaya.yaml import yaml_load_file, yaml_load

from IPython.display import Image

import importlib
import sys

import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='[%(name)s] %(message)s')




# Get the path of the current file
path_to_basedir = os.path.dirname(os.path.abspath(__file__))

# Construct the path to the APIs directory
path_to_apis = os.path.join(path_to_basedir, "apis")

# Construct the path to the assistants directory
path_to_assistants = os.path.join(path_to_basedir, "assistants")
path_to_engineer = os.path.join(path_to_basedir, "engineer")
path_to_planner = os.path.join(path_to_basedir, "planner")
path_to_executor = os.path.join(path_to_basedir, "executor")
path_to_admin = os.path.join(path_to_basedir, "admin")

work_dir = os.path.join(path_to_basedir, "../output")



default_chunking_strategy = {
    "type": "static",
    "static": {
        "max_chunk_size_tokens": 800, # reduce size to ensure better context integrity
        "chunk_overlap_tokens": 400 # increase overlap to maintain context across chunks
    }
}

# notes from https://platform.openai.com/docs/assistants/tools/file-search:

# max_chunk_size_tokens must be between 100 and 4096 inclusive.
# chunk_overlap_tokens must be non-negative and should not exceed max_chunk_size_tokens / 2.

# By default, the file_search tool outputs up to 20 chunks for gpt-4* models and up to 5 chunks for gpt-3.5-turbo. 
# You can adjust this by setting file_search.max_num_results in the tool when creating the assistant or the run.

default_top_p = 0.05
default_temperature = 0.00001