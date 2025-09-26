import os
import logging
import importlib
import requests
import autogen 
import json
import sys
import pandas as pd
import copy
import datetime
from pathlib import Path
import time
import pickle
from collections import defaultdict
from openai import OpenAI
from typing import List, Dict, Any
import glob
from IPython.display import Image
from autogen.agentchat.group import ContextVariables
from autogen.agentchat.group.patterns import AutoPattern

from .agents.planner_response_formatter.planner_response_formatter import save_final_plan
from .utils import work_dir_default
from .utils import default_llm_model as default_llm_model_default
from .utils import default_formatter_model as default_formatter_model_default
from .utils import clean_llm_config

from .utils import (path_to_assistants, path_to_apis,path_to_agents, update_yaml_preserving_format, get_model_config,
                    default_top_p, default_temperature, default_max_round,default_llm_config_list, default_agent_llm_configs,
                    default_agents_llm_model, camb_context_url,classy_context_url, AAS_keywords_string, get_api_keys_from_env)

from .rag_utils import import_rag_agents, push_vector_stores
from .hand_offs import register_all_hand_offs
from .functions import register_functions_to_agents
from .data_retriever import setup_cmbagent_data

from .keywords_utils import UnescoKeywords
from .keywords_utils import AaaiKeywords
from .utils import unesco_taxonomy_path, aaai_keywords_path

def import_non_rag_agents():
    imported_non_rag_agents = {}
    for subdir in os.listdir(path_to_agents):
        # Skip rag_agents folder and non-directories
        if subdir == "rag_agents":
            continue
        subdir_path = os.path.join(path_to_agents, subdir)
        if os.path.isdir(subdir_path):
            for filename in os.listdir(subdir_path):
                if filename.endswith(".py") and filename != "__init__.py" and filename[0] != ".":
                    module_name = filename[:-3]  # Remove the .py extension
                    class_name = ''.join([part.capitalize() for part in module_name.split('_')]) + 'Agent'
                    # Assuming the module path is agents.<subdir>.<module_name>
                    module_path = f"cmbagent.agents.{subdir}.{module_name}"
                    module = importlib.import_module(module_path)
                    agent_class = getattr(module, class_name)
                    imported_non_rag_agents[class_name] = {
                        'agent_class': agent_class,
                        'agent_name': module_name,
                    }
    return imported_non_rag_agents

from autogen import cmbagent_debug
from autogen.agentchat import initiate_group_chat
from cmbagent.context import shared_context as shared_context_default
import shutil

class CMBAgent:

    logging.disable(logging.CRITICAL)
    cmbagent_debug = autogen.cmbagent_debug

    def __init__(self,
                 cache_seed=None,
                 temperature=default_temperature,
                 top_p=default_top_p,
                 timeout=1200,
                 max_round=default_max_round,
                 platform='oai',
                 model='gpt4o',
                 llm_api_key=None,
                 llm_api_type=None,
                 make_vector_stores=False, #set to True to update all vector_stores, or a list of agents to update only those vector_stores e.g., make_vector_stores= ['cobaya', 'camb'].
                 agent_list = ['camb','classy_sz','cobaya','planck'],
                 verbose = False,
                 reset_assistant = False,
                 agent_instructions = {
                        "executor":
                        """
                        You execute python code provided to you by the engineer or save content provided by the researcher.
                        """,      
                    },
                 agent_descriptions = None,
                 agent_temperature = None,
                 agent_top_p = None,
                #  vector_store_ids = None,
                 chunking_strategy = {
                    'camb_agent': 
                    {
                    "type": "static",
                    "static": {
                      "max_chunk_size_tokens": 800, # reduce size to ensure better context integrity
                      "chunk_overlap_tokens": 200 # increase overlap to maintain context across chunks
                    }
                }
                },
                 select_speaker_prompt = None,
                 select_speaker_message = None,
                 intro_message = None,
                 set_allowed_transitions = None,
                 skip_executor = False,
                 skip_memory = True,
                 skip_rag_software_formatter = True,
                 skip_rag_agents = True,
                 default_llm_model = default_llm_model_default,
                 default_formatter_model = default_formatter_model_default,
                 default_llm_config_list = default_llm_config_list,
                 agent_llm_configs = default_agent_llm_configs,
                 agent_type = 'swarm',# None,# 'swarm',
                 shared_context = shared_context_default,
                 work_dir = work_dir_default,
                 clear_work_dir = True,
                 mode = "planning_and_control", # can be "one_shot" , "chat" or "planning_and_control" (default is planning and control), or "planning_and_control_context_carryover"
                 chat_agent = None,
                 api_keys = None,
                #  make_new_rag_agents = False, ## can be a list of names for new rag agents to be created
                 **kwargs):
        """
        Initialize the CMBAgent.

        Args:
            cache_seed (int, optional): Seed for caching. Defaults to 42.
            temperature (float, optional): Temperature for LLM sampling. Defaults to 0.
            timeout (int, optional): Timeout for LLM requests in seconds. Defaults to 1200.
            max_round (int, optional): Maximum number of conversation rounds. Defaults to 50. If too small, the conversation stops.
            llm_api_key (str, optional): API key for LLM. If None, uses the key from the config file.
            make_vector_stores (bool or list of strings, optional): Whether to create vector stores. Defaults to False. For only subset, use, e.g., make_vector_stores= ['cobaya', 'camb'].
            agent_list (list of strings, optional): List of agents to include in the conversation. Defaults to all agents.
            chunking_strategy (dict, optional): Chunking strategy for vector stores. Defaults to None.
            make_new_rag_agents (list of strings, optional): List of names for new rag agents to be created. Defaults to False.
            
            **kwargs: Additional keyword arguments.

        Attributes:
            kwargs (dict): Additional keyword arguments.
            work_dir (str): Working directory for output.
            path_to_assistants (str): Path to the assistants directory.
            llm_api_key (str): OpenAI API key.
            engineer (engineer_agent): Agent for engineering tasks.
            planner (planner_agent): Agent for planning tasks.
            executor (executor_agent): Agent for executing tasks.

        Note:
            This class initializes various agents and configurations for cosmological data analysis.
        """
        if default_llm_model != default_llm_model_default:

            default_llm_config_list = [get_model_config(default_llm_model, api_keys)]

        self.kwargs = kwargs

        self.skip_executor = skip_executor

        self.skip_rag_agents = skip_rag_agents

        if make_vector_stores is not False:
            self.skip_rag_agents = False

        self.skip_rag_software_formatter = skip_rag_software_formatter

        # self.make_new_rag_agents = make_new_rag_agents
        self.set_allowed_transitions = set_allowed_transitions

        self.vector_store_ids = None

        self.logger = logging.getLogger(__name__)

        # self.non_rag_agents = ['engineer', 'planner', 'executor', 'admin', 'summarizer', 'rag_software_formatter']

        self.agent_list = agent_list

        self.skip_memory = skip_memory

        self.results = {}

        self.mode = mode
        self.chat_agent = chat_agent

        if not self.skip_memory and 'memory' not in agent_list:
            self.agent_list.append('memory')

        self.verbose = verbose



        if work_dir != work_dir_default:
            # delete work_dir_default as it wont be used
            # exception if we are working within work_dir_default, i.e., work_dir is a subdirectory of work_dir_default
            if not work_dir_default.resolve() in work_dir.resolve().parents:
                shutil.rmtree(work_dir_default, ignore_errors=True)
            # shutil.rmtree(work_dir_default, ignore_errors=True)

        self.work_dir = os.path.expanduser(work_dir)
        self.clear_work_dir_bool = clear_work_dir
        if clear_work_dir:
            self.clear_work_dir()
        
        # add the work_dir to the python path so we can import modules from it
        sys.path.append(self.work_dir)

        self.path_to_assistants = path_to_assistants

        self.logger.info(f"Autogen version: {autogen.__version__}")

        llm_config_list = default_llm_config_list.copy()

        if llm_api_key is not None:
            llm_config_list[0]['api_key'] = llm_api_key

        if llm_api_type is not None:
            llm_config_list[0]['api_type'] = llm_api_type

        self.llm_api_key = llm_config_list[0]['api_key']
        self.openai_api_key = os.getenv("OPENAI_API_KEY")

        self.logger.info(f"Path to APIs: {path_to_apis}")

        self.cache_seed = cache_seed

        self.llm_config = {
                        "cache_seed": self.cache_seed,  # change the cache_seed for different trials
                        "temperature": temperature,
                        "top_p": top_p,
                        "config_list": llm_config_list,
                        "timeout": timeout,
                        "check_every_ms": None,
                    }
        
        if autogen.cmbagent_debug:
            print('\n\n\n\nin cmbagent.py self.llm_config: ',self.llm_config)

        # self.llm_config =  {"model": "gpt-4o-mini", "cache_seed": None}

        self.logger.info("LLM Configuration:")

        for key, value in self.llm_config.items():

            self.logger.info(f"{key}: {value}")

        self.agent_type = agent_type

        self.agent_llm_configs = default_agent_llm_configs.copy()
        self.agent_llm_configs.update(agent_llm_configs)

        if api_keys is not None:

            self.llm_config["config_list"][0] = get_model_config(self.llm_config["config_list"][0]["model"], api_keys)
            
            for agent in self.agent_llm_configs.keys():                
                self.agent_llm_configs[agent] = get_model_config(self.agent_llm_configs[agent]["model"], api_keys)


        self.api_keys = api_keys

        self.init_agents(agent_llm_configs=self.agent_llm_configs, default_formatter_model=default_formatter_model) # initialize agents

        if cmbagent_debug:
            print("\n\n All agents instantiated!!!\n\n")

        if cmbagent_debug:  
            print("\n\n Checking assistants...\n\n")

        if not self.skip_rag_agents:
            setup_cmbagent_data()

            self.check_assistants(reset_assistant=reset_assistant) # check if assistants exist

            if cmbagent_debug:
                print("\n\n Assistants checked!!!\n\n")

            if cmbagent_debug:
                print('\npushing vector stores...')
            push_vector_stores(self, make_vector_stores, chunking_strategy, verbose = verbose) # push vector stores

        if cmbagent_debug:
            print('\nsetting planner instructions currently not doing anything...')
            print('\nmodify if you want to tune the instruction prompt...')
        self.set_planner_instructions() # set planner instructions

        if self.verbose or cmbagent_debug:
            print("\nSetting up agents:----------------------------------")

        # then we set the agents, note that self.agents is set in init_agents
        for agent in self.agents:

            agent.agent_type = self.agent_type
            if cmbagent_debug:
                print(f"\t- {agent.name}")

            instructions = agent_instructions[agent.name] if agent_instructions and agent.name in agent_instructions else None
            description = agent_descriptions[agent.name] if agent_descriptions and agent.name in agent_descriptions else None
            agent_kwargs = {}

            if instructions is not None:
                agent_kwargs['instructions'] = instructions

            if description is not None:
                agent_kwargs['description'] = description
           

            if agent.name not in self.non_rag_agent_names: ## loop over all rag agents 
                if self.skip_rag_agents:
                    continue
                vector_ids = self.vector_store_ids[agent.name] if self.vector_store_ids and agent.name in self.vector_store_ids else None
                temperature = agent_temperature[agent.name] if agent_temperature and agent.name in agent_temperature else None
                top_p = agent_top_p[agent.name] if agent_top_p and agent.name in agent_top_p else None

                if vector_ids is not None:
                    agent_kwargs['vector_store_ids'] = vector_ids

                if temperature is not None:
                    agent_kwargs['agent_temperature'] = temperature
                else:
                    agent_kwargs['agent_temperature'] = default_temperature

                if top_p is not None:
                    agent_kwargs['agent_top_p'] = top_p
                else:
                    agent_kwargs['agent_top_p'] = default_top_p

                # cmbagent debug --> removed this option, pass in make_vector_stores=True in kwargs
                # #### the files list is appended twice to the instructions.... TBD!!!
                setagent = agent.set_agent(**agent_kwargs)

                if setagent == 1:

                    if cmbagent_debug:
                        print(f"setting make_vector_stores=['{agent.name.removesuffix('_agent')}'],")
                    
                    push_vector_stores(self, [agent.name.removesuffix('_agent')], chunking_strategy, verbose = verbose)

                    agent_kwargs['vector_store_ids'] = self.vector_store_ids[agent.name] 

                    
                    agent.set_agent(**agent_kwargs) 

                # else:
                # see above for trick on how to make vector store if it is not found. 
                # agent.set_agent(**agent_kwargs)

            else: ## set all non-rag agents
                
                agent.set_agent(**agent_kwargs)

            ## debug print to help debug
            #print('in cmbagent.py self.agents instructions: ',instructions)
            #print('in cmbagent.py self.agents description: ',description)

        if self.verbose or cmbagent_debug:
            print("Planner instructions:")
            print("\nAll agents:")
            for agent in self.agents:
                print("\n\n----------------------------------")
                print(f"- {agent.name}")
                print(dir(agent))
                print("\n\n----------------------------------")
            print()
            planner = self.get_agent_object_from_name('planner')
            print(planner.info['instructions'])

        if cmbagent_debug:
            print('\nregistering all hand_offs...')

        register_all_hand_offs(self)

        if cmbagent_debug:
            print('\nall hand_offs registered...')


        if cmbagent_debug:
            print('\nadding functions to agents...')

        register_functions_to_agents(self)

        if cmbagent_debug:
            print('\nfunctions added to agents...')

        self.shared_context = shared_context_default
        if shared_context is not None:
            self.shared_context.update(shared_context)

        if cmbagent_debug:
            print('\nshared_context: ', self.shared_context)

    def display_cost(self, name_append = None):
        """Display a full cost report as a right‑aligned Markdown table with $ and a
        rule above the total row. Also saves the cost data as JSON in the workdir."""
        import json

        cost_dict = defaultdict(list)

        # --- collect per‑agent costs ------------------------------------------------
        all_agents = [a.agent for a in self.agents]
        if hasattr(self, "groupchat"):
            all_agents += self.groupchat.new_conversable_agents

        for agent in all_agents:
            if hasattr(agent, "cost_dict") and agent.cost_dict.get("Agent"):
                name = (
                    agent.cost_dict["Agent"][0]
                    .replace("admin (", "")
                    .replace(")", "")
                    .replace("_", " ")
                )
                summed_cost   = round(sum(agent.cost_dict["Cost"]), 8)
                summed_prompt = int(sum(agent.cost_dict["Prompt Tokens"]))
                summed_comp   = int(sum(agent.cost_dict["Completion Tokens"]))
                summed_total  = int(sum(agent.cost_dict["Total Tokens"]))

                model_name = agent.cost_dict["Model"][0]
                

                if name in cost_dict["Agent"]:
                    i = cost_dict["Agent"].index(name)
                    cost_dict["Cost ($)"][i]          += summed_cost
                    cost_dict["Prompt Tokens"][i]     += summed_prompt
                    cost_dict["Completion Tokens"][i] += summed_comp
                    cost_dict["Total Tokens"][i]      += summed_total
                    cost_dict["Model"][i]             += model_name
                else:
                    cost_dict["Agent"].append(name)
                    cost_dict["Cost ($)"].append(summed_cost)
                    cost_dict["Prompt Tokens"].append(summed_prompt)
                    cost_dict["Completion Tokens"].append(summed_comp)
                    cost_dict["Total Tokens"].append(summed_total)
                    cost_dict["Model"].append(model_name)

        # --- build DataFrame & totals ----------------------------------------------
        df = pd.DataFrame(cost_dict)
        numeric_cols = df.select_dtypes(include="number").columns
        totals = df[numeric_cols].sum()
        df.loc["Total"] = pd.concat([pd.Series({"Agent": "Total"}), totals])

        # --- string formatting for display ------------------------------------------------------
        df_str = df.copy()
        df_str["Cost ($)"] = df_str["Cost ($)"].map(lambda x: f"${x:.8f}")
        for col in ["Prompt Tokens", "Completion Tokens", "Total Tokens"]:
            df_str[col] = df_str[col].astype(int).astype(str)

        columns = df_str.columns.tolist()
        rows = df_str.fillna("").values.tolist()

        # --- column widths ----------------------------------------------------------
        widths = [
            max(len(col), max(len(str(row[i])) for row in rows))
            for i, col in enumerate(columns)
        ]

        # --- header with alignment markers -----------------------------------------
        header   = "|" + "|".join(f" {columns[i].ljust(widths[i])} " for i in range(len(columns))) + "|"

        # Markdown alignment row: left for text, right for numbers
        align_row = []
        for i, col in enumerate(columns):
            if col == "Agent":
                align_row.append(":" + "-"*(widths[i]+1))      # :---- for left
            else:
                align_row.append("-"*(widths[i]+1) + ":")      # ----: for right
        separator = "|" + "|".join(align_row) + "|"

        # --- build data lines -------------------------------------------------------
        lines = [header, separator]
        for idx, row in enumerate(rows):
            # insert rule before the Total row
            if row[0] == "Total":
                lines.append("|" + "|".join("-"*(widths[i]+2) for i in range(len(columns))) + "|")

            cell = []
            for i, col in enumerate(columns):
                s = str(row[i])
                if col == "Agent":
                    cell.append(f" {s.ljust(widths[i])} ")
                else:
                    cell.append(f" {s.rjust(widths[i])} ")
            lines.append("|" + "|".join(cell) + "|")

        print("\nDisplaying cost…\n")
        print("\n".join(lines))

        self.final_context['cost_dataframe'] = df

        # --- Save cost data as JSON ------------------------------------------------
        # Convert DataFrame to dict for JSON serialization
        cost_data = df.to_dict(orient='records')
        
        # Add timestamp
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Save to JSON file in workdir
        if name_append is not None:
            json_path = os.path.join(self.work_dir, f"cost/cost_report_{name_append}_{timestamp}.json")
        else:
            json_path = os.path.join(self.work_dir, f"cost/cost_report_{timestamp}.json")
        with open(json_path, 'w') as f:
            json.dump(cost_data, f, indent=2)
            
        print(f"\nCost report data saved to: {json_path}\n")

        self.final_context['cost_report_path'] = json_path
        
        return df

        

    def clear_work_dir(self):
        # Clear everything inside work_dir if it exists
        if os.path.exists(self.work_dir):
            for item in os.listdir(self.work_dir):
                item_path = os.path.join(self.work_dir, item)
                if os.path.isfile(item_path):
                    os.unlink(item_path)
                elif os.path.isdir(item_path):
                    shutil.rmtree(item_path)


    def solve(self, task, 
              initial_agent='task_improver', 
              shared_context=None,
              mode = "default", # can be "one_shot" or "default" (default is planning and control)
              step = None,
              max_rounds=10):
        self.step = step ## record the step for the context carryover workflow 
        this_shared_context = copy.deepcopy(self.shared_context)
        
        if mode == "one_shot" or mode == "chat":
            one_shot_shared_context = {'final_plan': "Step 1: solve the main task.",
                                        'current_status': "In progress",
                                        'current_plan_step_number': 1,
                                        'current_sub_task' : "solve the main task.",
                                        'current_instructions': "solve the main task.",
                                        'agent_for_sub_task': initial_agent,
                                        'feedback_left': 0,
                                        "number_of_steps_in_plan": 1,
                                        'maximum_number_of_steps_in_plan': 1,
                                        'researcher_append_instructions': '',
                                        'engineer_append_instructions': '',
                                        'perplexity_append_instructions': '',
                                        'idea_maker_append_instructions': '',
                                        'idea_hater_append_instructions': '',
                                        }
            
            if initial_agent == 'perplexity':
                one_shot_shared_context['perplexity_query'] = self.get_agent_object_from_name('perplexity').info['instructions'].format(main_task=task)
                # print('one_shot_shared_context: ', one_shot_shared_context)

            this_shared_context.update(one_shot_shared_context)
            this_shared_context.update(shared_context or {})

            # print('one_shot_shared_context: ', one_shot_shared_context)
            # print('shared_context: ', this_shared_context)
            # sys.exit()
            
        else:
            if shared_context is not None:
                this_shared_context.update(shared_context)
        
        try:
            self.clear_cache() ## obsolete
            # import pdb; pdb.set_trace()
        except:
            pass
        if self.clear_work_dir_bool:
            self.clear_work_dir()

        # Define full paths
        database_full_path = os.path.join(self.work_dir, this_shared_context.get("database_path", "data"))
        codebase_full_path = os.path.join(self.work_dir, this_shared_context.get("codebase_path", "codebase"))

        # add the codebase to the python path so we can import modules from it
        sys.path.append(codebase_full_path)

        chat_full_path = os.path.join(self.work_dir, "chats")
        time_full_path = os.path.join(self.work_dir, "time")
        cost_full_path = os.path.join(self.work_dir, "cost")
        
        # Create directories if they don't exist
        os.makedirs(database_full_path, exist_ok=True)
        os.makedirs(codebase_full_path, exist_ok=True)

        os.makedirs(chat_full_path, exist_ok=True)
        os.makedirs(time_full_path, exist_ok=True)
        os.makedirs(cost_full_path, exist_ok=True)

        for agent in self.agents:
            try:
                agent.agent.reset()
            except:
                pass

        this_shared_context['main_task'] = task
        this_shared_context['improved_main_task'] = task # initialize improved main task

        this_shared_context['work_dir'] = self.work_dir
        # print('this_shared_context: ', this_shared_context)
        # sys.exit()

        context_variables = ContextVariables(data=this_shared_context)

        # Create the pattern
        agent_pattern = AutoPattern(
                agents=[agent.agent for agent in self.agents],
                initial_agent=self.get_agent_from_name(initial_agent),
                context_variables=context_variables,
                group_manager_args = {"llm_config": self.llm_config, 
                                      "name": "main_cmbagent_chat"},
            )

        chat_result, context_variables, last_agent = initiate_group_chat(
            pattern=agent_pattern,
            messages=this_shared_context['main_task'],
            # user_agent=self.get_agent_from_name("admin"),
            max_rounds = max_rounds,
        )

        self.final_context = copy.deepcopy(context_variables)

        self.last_agent = last_agent
        self.chat_result = chat_result


    def get_agent_object_from_name(self,name):
        for agent in self.agents:
            if agent.info['name'] == name:
                return agent
        print(f"get_agent_object_from_name: agent {name} not found")
        sys.exit()

    def get_agent_from_name(self,name):
        for agent in self.agents:
            if agent.info['name'] == name:
                return agent.agent
        print(f"get_agent_from_name: agent {name} not found")
        sys.exit()

    def init_agents(self,agent_llm_configs=None, default_formatter_model=default_formatter_model_default):

        # this automatically loads all the agents from the assistants folder
        imported_rag_agents = import_rag_agents()
        imported_non_rag_agents = import_non_rag_agents()
        # print('imported_rag_agents: ', imported_rag_agents)
        # print("making new rag agents: ", self.make_new_rag_agents)
        # make_rag_agents(self.make_new_rag_agents)
        # imported_rag_agents = import_rag_agents()
        # print('imported_rag_agents: ', imported_rag_agents)

        ## this will store classes for each agents
        self.agent_classes = {}
        self.rag_agent_names = []
        self.non_rag_agent_names = []

        for k in imported_rag_agents.keys():
            self.agent_classes[imported_rag_agents[k]['agent_name']] = imported_rag_agents[k]['agent_class']
            self.rag_agent_names.append(imported_rag_agents[k]['agent_name'])

        for k in imported_non_rag_agents.keys():
            self.agent_classes[imported_non_rag_agents[k]['agent_name']] = imported_non_rag_agents[k]['agent_class']
            self.non_rag_agent_names.append(imported_non_rag_agents[k]['agent_name'])

        if cmbagent_debug:
            print('self.agent_classes: ', self.agent_classes)
            print('self.rag_agent_names: ', self.rag_agent_names)
            print('self.non_rag_agent_names: ', self.non_rag_agent_names)

        if cmbagent_debug:
            print('self.agent_classes after update: ')
            print()
            for agent_class, value in self.agent_classes.items():
                print(f'{agent_class}: {value}')
                print()

        # all agents
        self.agents = []

        if self.agent_list is None:
            self.agent_list = list(self.agent_classes.keys())

        # Drop entries from self.agent_classes that are not in self.agent_list
        self.agent_classes = {k: v for k, v in self.agent_classes.items() if k in self.agent_list or k in self.non_rag_agent_names}

        if cmbagent_debug:
            print('self.agent_classes after list update: ')
            print()
            for agent_class, value in self.agent_classes.items():
                print(f'{agent_class}: {value}')
                print()

        # remove agents that are not set to be skipped
        if self.skip_memory:
            # self.agent_classes.pop('memory')
            self.agent_classes.pop('session_summarizer')
        
        if self.skip_executor:
            self.agent_classes.pop('executor')

        if self.skip_rag_software_formatter:
            self.agent_classes.pop('rag_software_formatter')

        if cmbagent_debug:
            print('self.agent_classes after skipping agents: ')
            print()
            for agent_class, value in self.agent_classes.items():
                print(f'{agent_class}: {value}')
                print()

        # instantiate the agents and llm_configs
        if cmbagent_debug:
            print('self.llm_config: ', self.llm_config)


        for agent_name  in self.agent_classes:
            agent_class = self.agent_classes[agent_name]

            if cmbagent_debug:
                print('instantiating agent: ', agent_name)

            if agent_name in agent_llm_configs:
                llm_config = copy.deepcopy(self.llm_config)
                llm_config['config_list'][0].update(agent_llm_configs[agent_name])
                clean_llm_config(llm_config)
                
                if cmbagent_debug:
                    print('in cmbagent.py: found agent_llm_configs for: ', agent_name)
                    print('in cmbagent.py: llm_config updated to: ', llm_config)
            else:
                llm_config = copy.deepcopy(self.llm_config)

            if cmbagent_debug:
                print('in cmbagent.py BEFORE agent_instance: llm_config: ', llm_config)

            agent_instance = agent_class(llm_config=llm_config,agent_type=self.agent_type, work_dir=self.work_dir)

            if cmbagent_debug:
                print('agent_type: ', agent_instance.agent_type)

            # setattr(self, agent_name, agent_instance)

            self.agents.append(agent_instance)

        if self.skip_rag_agents:
            self.agents = [agent for agent in self.agents if agent.name.replace('_agent', '') not in self.rag_agent_names]

        # print('rag_agent_names: ', self.rag_agent_names)
        self.agent_names =  [agent.name for agent in self.agents]
        # print('skip_rag_agents: ', self.skip_rag_agents)
        # print('self.agents: ', self.agent_names)
        # import sys 
        # sys.exit()

        if cmbagent_debug:
            for agent in self.agents:
                print('\n\nagent.name: ', agent.name)
                print('agent.llm_config: ', agent.llm_config)
                print('\n\n')

        for agent in self.agents:

            if "formatter" in agent.name:

                # print("="*10)
                # print('\n\nagent.name BEFORE: ', agent.name)
                # print('agent.llm_config: ', agent.llm_config)
                # print('\n\n')

                agent.llm_config['config_list'][0].update(get_model_config(default_formatter_model, self.api_keys))
                
                # print('\n\nagent.name AFTER: ', agent.name)
                # print('agent.llm_config: ', agent.llm_config)
                # print('\n\n')
            # make sure the llm config doesnt have inconsistent parameters
            clean_llm_config(agent.llm_config)


        if self.verbose or cmbagent_debug:

            print("Using following agents: ", self.agent_names)
            print("Using following llm for agents: ")
            for agent in self.agents:
                print(f"{agent.name}: {agent.llm_config['config_list'][0]['model']}")
            print()

    def create_assistant(self, client, agent):

        if cmbagent_debug:
            print(f"-->Creating assistant {agent.name}")
            print(f"--> llm_config: {self.llm_config}")
            print(f"--> agent.llm_config: {agent.llm_config}")

        new_assistant = client.beta.assistants.create(
            name=agent.name,
            instructions=agent.info['instructions'],
            tools=[{"type": "file_search"}],
            tool_resources={"file_search": {"vector_store_ids":[]}},
            model=agent.llm_config['config_list'][0]['model'],
            # tool_choice={"type": "function", "function": {"name": "file_search"}}, ## not possible to set tool_choice as argument as of 8/03/2025
            # response_format=agent.llm_config['config_list'][0]['response_format']
        )

        if cmbagent_debug:
            print("New assistant created.")
            print(f"--> New assistant id: {new_assistant.id}")
            print(f"--> New assistant model: {new_assistant.model}")
            # print(f"--> New assistant response format: {new_assistant.response_format}")
            # print(f"--> New assistant tool choice: {new_assistant.tool_choice}")
            print("\n")

        return new_assistant


    def check_assistants(self, reset_assistant=[]):

        client = OpenAI(api_key = self.openai_api_key)
        available_assistants = client.beta.assistants.list(
            order="desc",
            limit="100",
        )


        # Create a list of assistant names for easy comparison
        assistant_names = [d.name for d in available_assistants.data]
        assistant_ids = [d.id for d in available_assistants.data]
        assistant_models = [d.model for d in available_assistants.data]

        for agent in self.agents:

            if cmbagent_debug:
                print('in cmbagent.py check_assistants: agent: ', agent.name)
                print('non_rag_agent_names: ', self.non_rag_agent_names)

            if agent.name not in self.non_rag_agent_names:
                if cmbagent_debug:
                    print(f"Checking agent: {agent.name}")

                # Check if agent name exists in the available assistants
                if agent.name in assistant_names:
                    if cmbagent_debug:
                        print(f"in cmbagent.py check_assistants: Agent {agent.name} exists in available assistants with id: {assistant_ids[assistant_names.index(agent.name)]}")

                    if cmbagent_debug:
                        print('in cmbagent.py check_assistants: this assistant model from openai: ',assistant_models[assistant_names.index(agent.name)])
                        print('in cmbagent.py check_assistants: this assistant model from llm_config: ', agent.llm_config['config_list'][0]['model'])
                    if assistant_models[assistant_names.index(agent.name)] != agent.llm_config['config_list'][0]['model']:
                        if cmbagent_debug:
                            print("in cmbagent.py check_assistants: Assistant model from openai does not match the requested model. Updating the assistant model.")
                        client.beta.assistants.update(
                            assistant_id=assistant_ids[assistant_names.index(agent.name)],
                            model=agent.llm_config['config_list'][0]['model']
                        )

                    if reset_assistant and agent.name.replace('_agent', '') in reset_assistant:
                        
                        print("This agent is in the reset_assistant list. Resetting the assistant.")
                        print("Deleting the assistant...")
                        client.beta.assistants.delete(assistant_ids[assistant_names.index(agent.name)])
                        print("Assistant deleted. Creating a new one...")
                        new_assistant = self.create_assistant(client, agent)
                        agent.info['assistant_config']['assistant_id'] = new_assistant.id
                        

                    else:

                        assistant_id = agent.info['assistant_config']['assistant_id']

                        if assistant_id != assistant_ids[assistant_names.index(agent.name)]:
                            if cmbagent_debug:
                                print("--> Assistant ID between yaml and openai do not match.")
                                print(f"--> Assistant ID from your yaml: {assistant_id}")
                                print(f"--> Assistant ID in openai: {assistant_ids[assistant_names.index(agent.name)]}")
                                print("--> We will use the assistant id from openai")
                            

                            agent.info['assistant_config']['assistant_id'] = assistant_ids[assistant_names.index(agent.name)]
                            if cmbagent_debug:
                                print(f"--> Updating yaml file with new assistant id: {assistant_ids[assistant_names.index(agent.name)]}")
                            update_yaml_preserving_format(f"{path_to_assistants}/{agent.name.replace('_agent', '') }.yaml", agent.name, assistant_ids[assistant_names.index(agent.name)], field = 'assistant_id')
                    
                else:

                    new_assistant = self.create_assistant(client, agent)
                    agent.info['assistant_config']['assistant_id'] = new_assistant.id



    def show_plot(self,plot_name):

        return Image(filename=self.work_dir + '/' + plot_name)


    def clear_cache(self):
        # print('clearing cache...')
        cache_dir = autogen.oai.client.LEGACY_CACHE_DIR ## "./cache" 
        # print("cache_dir: ", cache_dir)
        # if os.path.exists(cache_dir):
        #     # print("found cache_dir: ", cache_dir)
        #     shutil.rmtree(cache_dir)
            # print("cache_dir removed")
        # else:
            # print("no cache_dir found...")
        return None
        #  autogen.Completion.clear_cache(self.cache_seed) ## obsolete AttributeError: module 'autogen' has no attribute 'Completion'



    def filter_and_combine_agent_names(self, input_list):
        # Filter the input list to include only entries in self.agent_names
        filtered_list = [item for item in input_list if item in self.agent_names]

        # Convert the filtered list of strings into one string
        combined_string = ', '.join(filtered_list)

        return combined_string


    def set_planner_instructions(self):
        ### this is a template. Currently not used.

        # available agents and their roles:
        # available_agents = "\n\n#### Available agents and their roles\n\n"
        
        # for agent in self.agents:

        #     if agent.name in ['planner', 'engineer', 'executor', 'admin']:
        #         continue


        #     if 'description' in agent.info:

        #         role = agent.info['description']

        #     else:

        #         role = agent.info['instructions']

        #     available_agents += f"- *{agent.name}* : {role}\n"


        # # collect allowed transitions
        # all_allowed_transitions = "\n\n#### Allowed transitions\n\n"

        # for agent in self.agents:

        #     all_allowed_transitions += f"\t- {agent.name} -> {self.filter_and_combine_agent_names(agent.info['allowed_transitions'])}\n"



        # commenting for now
        # self.planner.info['instructions'] += available_agents + '\n\n' #+ all_allowed_transitions

        return


def clean_work_dir(work_dir):
    # Clear everything inside work_dir if it exists
    if os.path.exists(work_dir):
        for item in os.listdir(work_dir):
            item_path = os.path.join(work_dir, item)
            if os.path.isfile(item_path):
                os.unlink(item_path)
            elif os.path.isdir(item_path):
                shutil.rmtree(item_path)


def planning_and_control_context_carryover(
                            task,
                            max_rounds_planning = 50,
                            max_rounds_control = 100,
                            max_plan_steps = 3,
                            n_plan_reviews = 1,
                            plan_instructions = '',
                            engineer_instructions = '', # append to engineer instructions
                            researcher_instructions = '', # append to researcher instructions
                            hardware_constraints = '', 
                            max_n_attempts = 3,
                            planner_model = default_agents_llm_model['planner'],
                            plan_reviewer_model = default_agents_llm_model['plan_reviewer'],
                            engineer_model = default_agents_llm_model['engineer'],
                            researcher_model = default_agents_llm_model['researcher'],
                            idea_maker_model = default_agents_llm_model['idea_maker'],
                            idea_hater_model = default_agents_llm_model['idea_hater'],
                            camb_context_model = default_agents_llm_model['camb_context'],
                            plot_judge_model = default_agents_llm_model['plot_judge'],
                            default_llm_model = default_llm_model_default,
                            default_formatter_model = default_formatter_model_default,
                            work_dir = work_dir_default,
                            api_keys = None,
                            restart_at_step = -1,   ## if -1 or 0, do not restart. if 1, restart from step 1, etc.
                            clear_work_dir = False,
                            researcher_filename = shared_context_default['researcher_filename'],
                            ):

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
    

        cmbagent = CMBAgent(work_dir = planning_dir,
                            default_llm_model = default_llm_model,
                            default_formatter_model = default_formatter_model,
                            agent_llm_configs = {
                                'planner': planner_config,
                                'plan_reviewer': plan_reviewer_config,
                            },
                            api_keys = api_keys
                            )
        end_time = time.time()
        initialization_time_planning = end_time - start_time

        start_time = time.time()

        cmbagent.solve(task,
                    max_rounds=max_rounds_planning,
                    initial_agent="plan_setter",
                    shared_context = {'feedback_left': n_plan_reviews,
                                        'max_n_attempts': max_n_attempts,
                                        'maximum_number_of_steps_in_plan': max_plan_steps,
                                        'planner_append_instructions': plan_instructions,
                                        'engineer_append_instructions': engineer_instructions,
                                        'researcher_append_instructions': researcher_instructions,
                                        'plan_reviewer_append_instructions': plan_instructions,
                                        'hardware_constraints': hardware_constraints,
                                        'researcher_filename': researcher_filename}
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

        context_path = os.path.join(context_dir, "context_step_0.pkl") # save the initial context (0: planning)
        # with open(context_path, 'w') as f:
        #     json.dump(current_context, f, indent=2)
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
        # Save to JSON file in workdir
        timing_path = os.path.join(planning_output['work_dir'], f"time/timing_report_planning_{timestamp}.json")
        with open(timing_path, 'w') as f:
            json.dump(timing_report, f, indent=2)
        
        print(f"\nTiming report data saved to: {timing_path}\n")


        ## delete empty folders during planning
        database_full_path = os.path.join(planning_output['work_dir'], planning_output['database_path'])
        codebase_full_path = os.path.join(planning_output['work_dir'], planning_output['codebase_path'])
        for folder in [database_full_path, codebase_full_path]:
            if not os.listdir(folder):
                os.rmdir(folder)


    
    ## control
    engineer_config = get_model_config(engineer_model, api_keys)
    researcher_config = get_model_config(researcher_model, api_keys)
    camb_context_config = get_model_config(camb_context_model, api_keys)
    idea_maker_config = get_model_config(idea_maker_model, api_keys)
    idea_hater_config = get_model_config(idea_hater_model, api_keys)
    plot_judge_config = get_model_config(plot_judge_model, api_keys)
        
    control_dir = Path(work_dir).expanduser().resolve() / "control"
    control_dir.mkdir(parents=True, exist_ok=True)

    current_context = copy.deepcopy(planning_output) if restart_at_step <= 0 else load_context(os.path.join(context_dir, f"context_step_{restart_at_step-1}.pkl"))
    number_of_steps_in_plan = current_context['number_of_steps_in_plan']
    step_summaries = []  
    # print("in cmbagent.py: current_context before step loop: ", current_context)
    initial_step = 1 if restart_at_step <= 0 else restart_at_step
    for step in range(initial_step, number_of_steps_in_plan + 1):
        clear_work_dir = True if step == 1 and restart_at_step <= 0 else False ## not fully sure what is the best thing to do, but this is OK for now.
        starter_agent = "control" if step == 1 else "control_starter"
        # print(f"in cmbagent.py: step: {step}/{number_of_steps_in_plan}")
        # print("\n\n")
        # print("current_context['previous_steps_execution_summary']: ", current_context['previous_steps_execution_summary'] )
        # print("\n\n")


        start_time = time.time()
        cmbagent = CMBAgent(
            work_dir = control_dir,
            clear_work_dir = clear_work_dir,
            default_llm_model = default_llm_model,
            default_formatter_model = default_formatter_model,
            agent_llm_configs = {
                                'engineer': engineer_config,
                                'researcher': researcher_config,
                                'idea_maker': idea_maker_config,
                                'idea_hater': idea_hater_config,
                                'camb_context': camb_context_config,
                                'plot_judge': plot_judge_config,
            },
            mode = "planning_and_control_context_carryover",
            api_keys = api_keys
            )
        

        # print(f"in cmbagent.py: idea_maker_config: {idea_maker_config}")
        # print(f"in cmbagent.py: idea_hater_config: {idea_hater_config}")
        
        end_time = time.time()
        initialization_time_control = end_time - start_time
        
        if step == 1:
            plan_input = load_plan(os.path.join(work_dir, "planning/final_plan.json"))["sub_tasks"]
            agent_for_step = plan_input[0]['sub_task_agent']
        else:
            agent_for_step = current_context['agent_for_sub_task']
        
       

        # import sys 
        # sys.exit()

        parsed_context = copy.deepcopy(current_context)

        parsed_context["agent_for_sub_task"] = agent_for_step
        parsed_context["current_plan_step_number"] = step
        parsed_context["n_attempts"] = 0 ## reset number of failures for each step. 
        # print(f"\nin cmbagent.py: agent_for_step {step}: {agent_for_step}")
        # print("xo"*100+"\n\n")
        # print("in cmbagent.py: parsed_context: ", parsed_context["final_plan"])
        # print("in cmbagent.py: parsed_context: ", parsed_context["number_of_steps_in_plan"])
        # print("xo"*100+"\n\n")
        # import sys
        # sys.exit()
            

        start_time = time.time()   

        cmbagent.solve(task,
                        max_rounds=max_rounds_control,
                        initial_agent=starter_agent,
                        shared_context = parsed_context,
                        step = step
                        )
        

        end_time = time.time()
        execution_time_control = end_time - start_time

        # number of failures:

        number_of_failures = cmbagent.final_context['n_attempts']

        
        results = {'chat_history': cmbagent.chat_result.chat_history,
                   'final_context': cmbagent.final_context}
        
        if number_of_failures >= cmbagent.final_context['max_n_attempts']:
            print(f"in cmbagent.py: number of failures: {number_of_failures} >= max_n_attempts: {cmbagent.final_context['max_n_attempts']}. Exiting.")
            break
        # print("_"*100+"\n\n")
        # print("in cmbagent.py: collecting step summaries for step: ", step)
        for msg in results['chat_history'][::-1]:
            # print("\nin cmbagent.py: msg: ", msg)
            if 'name' in msg:
                # print("\nin cmbagent.py: msg['name']: ", msg['name'])
                agent_for_step = agent_for_step.removesuffix("_context")
                agent_for_step = agent_for_step.removesuffix("_agent")
                if msg['name'] == agent_for_step or msg['name'] == f"{agent_for_step}_nest" or msg['name'] == f"{agent_for_step}_response_formatter":
                    # print("\nin cmbagent.py: msg['content']: ", msg['content'])
                    this_step_execution_summary = msg['content']
                    # build this step’s summary
                    summary = f"### Step {step}\n{this_step_execution_summary.strip()}"
                    step_summaries.append(summary)  
                    cmbagent.final_context['previous_steps_execution_summary'] = "\n\n".join(step_summaries)
                    break
        # print("in cmbagent.py: step_summaries: ", step_summaries)
        # print("_"*100+"\n\n")
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
        
        # Save to JSON file in workdir
        timing_path = os.path.join(current_context['work_dir'], f"time/timing_report_step_{step}_{timestamp}.json")
        with open(timing_path, 'w') as f:
            json.dump(timing_report, f, indent=2)
        
        print(f"\nTiming report data saved to: {timing_path}\n")

        
        # Create a dummy groupchat attribute if it doesn't exist
        if not hasattr(cmbagent, 'groupchat'):
            Dummy = type('Dummy', (object,), {'new_conversable_agents': []})
            cmbagent.groupchat = Dummy()

        # Now call display_cost without triggering the AttributeError
        cmbagent.display_cost(name_append = f"step_{step}")

        ## save the chat history and the final context
        chat_full_path = os.path.join(current_context['work_dir'], "chats")
        os.makedirs(chat_full_path, exist_ok=True)
        chat_output_path = os.path.join(chat_full_path, f"chat_history_step_{step}.json")
        with open(chat_output_path, 'w') as f:
            json.dump(results['chat_history'], f, indent=2)
        context_path = os.path.join(context_dir, f"context_step_{step}.pkl")
        # with open(context_path, 'w') as f:
        #     json.dump(current_context, f, indent=2)
        with open(context_path, 'wb') as f:
            pickle.dump(cmbagent.final_context, f)

        # if step == 4:
        #     break

    ## delete empty folders during planning
    database_full_path = os.path.join(current_context['work_dir'], current_context['database_path'])
    codebase_full_path = os.path.join(current_context['work_dir'], current_context['codebase_path'])
    for folder in [database_full_path, codebase_full_path]:
        if not os.listdir(folder):
            os.rmdir(folder)


    return results

def load_context(context_path):
    with open(context_path, 'rb') as f:
        context = pickle.load(f)
    return context

def planning_and_control(
                            task,
                            max_rounds_planning = 50,
                            max_rounds_control = 100,
                            max_plan_steps = 3,
                            n_plan_reviews = 1,
                            plan_instructions = '',
                            engineer_instructions = '',
                            researcher_instructions = '',
                            hardware_constraints = '',
                            max_n_attempts = 3,
                            planner_model = default_agents_llm_model['planner'],
                            plan_reviewer_model = default_agents_llm_model['plan_reviewer'],
                            engineer_model = default_agents_llm_model['engineer'],
                            researcher_model = default_agents_llm_model['researcher'],
                            idea_maker_model = default_agents_llm_model['idea_maker'],
                            idea_hater_model = default_agents_llm_model['idea_hater'],
                            work_dir = work_dir_default,
                            researcher_filename = shared_context_default['researcher_filename'],
                            default_llm_model = default_llm_model_default,
                            default_formatter_model = default_formatter_model_default,
                            api_keys = None,
                            ):

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
    
    cmbagent = CMBAgent(work_dir = planning_dir,
                        default_llm_model = default_llm_model,
                        default_formatter_model = default_formatter_model,
                        agent_llm_configs = {
                            'planner': planner_config,
                            'plan_reviewer': plan_reviewer_config,
                        },
                        api_keys = api_keys
                        )
    end_time = time.time()
    initialization_time_planning = end_time - start_time



    start_time = time.time()
    cmbagent.solve(task,
                max_rounds=max_rounds_planning,
                initial_agent="plan_setter",
                shared_context = {'feedback_left': n_plan_reviews,
                                    'max_n_attempts': max_n_attempts,
                                    'maximum_number_of_steps_in_plan': max_plan_steps,
                                    'planner_append_instructions': plan_instructions,
                                    'engineer_append_instructions': engineer_instructions,
                                    'researcher_append_instructions': researcher_instructions,
                                    'plan_reviewer_append_instructions': plan_instructions,
                                    'hardware_constraints': hardware_constraints,
                                    'researcher_filename': researcher_filename}
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
    # Save to JSON file in workdir
    timing_path = os.path.join(planning_output['work_dir'], f"time/timing_report_planning_{timestamp}.json")
    with open(timing_path, 'w') as f:
        json.dump(timing_report, f, indent=2)
    
    print(f"\nTiming report data saved to: {timing_path}\n")

    ## delete empty folders during control
    database_full_path = os.path.join(planning_output['work_dir'], planning_output['database_path'])
    codebase_full_path = os.path.join(planning_output['work_dir'], planning_output['codebase_path'])
    time_full_path = os.path.join(planning_output['work_dir'],'time')
    for folder in [database_full_path, codebase_full_path, time_full_path]:
        if not os.listdir(folder):
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
        work_dir = control_dir,
        default_llm_model = default_llm_model,
        default_formatter_model = default_formatter_model,
        agent_llm_configs = {
                            'engineer': engineer_config,
                            'researcher': researcher_config,
                            'idea_maker': idea_maker_config,
                            'idea_hater': idea_hater_config,
        },
        api_keys = api_keys
        )
    

    # print(f"in cmbagent.py: idea_maker_config: {idea_maker_config}")
    # print(f"in cmbagent.py: idea_hater_config: {idea_hater_config}")
    
    end_time = time.time()
    initialization_time_control = end_time - start_time
        

    start_time = time.time()    
    cmbagent.solve(task,
                    max_rounds=max_rounds_control,
                    initial_agent="control",
                    shared_context = planning_output
                    )
    end_time = time.time()
    execution_time_control = end_time - start_time
    
    results = {'chat_history': cmbagent.chat_result.chat_history,
               'final_context': cmbagent.final_context}
    
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
    time_full_path = os.path.join(results['final_context']['work_dir'],'time')
    for folder in [database_full_path, codebase_full_path, time_full_path]:
        if not os.listdir(folder):
            os.rmdir(folder)
    return results


def load_plan(plan_path):
    """Load a plan from a JSON file into a dictionary"""
    plan_path = os.path.expanduser(plan_path)  # Expands '~' 
    with open(plan_path, 'r') as f:
        plan_dict = json.load(f)
    
    return plan_dict


def _parse_formatted_content(content):
    """
    Parse the formatted markdown content from summarizer_response_formatter to extract structured data.
    The content is in markdown format with specific sections.
    """
    import re
    
    summary_data = {}
    
    try:
        # Extract title (first heading)
        title_match = re.search(r'^# (.+)', content, re.MULTILINE)
        if title_match:
            summary_data['title'] = title_match.group(1).strip()
        
        # Extract authors
        authors_match = re.search(r'\*\*Authors:\*\*\s*(.+)', content)
        if authors_match:
            authors_text = authors_match.group(1).strip()
            summary_data['authors'] = [author.strip() for author in authors_text.split(',')]
        
        # Extract date  
        date_match = re.search(r'\*\*Date:\*\*\s*(.+)', content)
        if date_match:
            summary_data['date'] = date_match.group(1).strip()

        # Extract journal
        journal_match = re.search(r'\*\*Journal:\*\*\s*(.+)', content)
        if journal_match:
            summary_data['journal'] = journal_match.group(1).strip()
        
        # Extract abstract
        abstract_match = re.search(r'\*\*Abstract:\*\*\s*(.+?)(?=\n\n\*\*|\n\*\*|\Z)', content, re.DOTALL)
        if abstract_match:
            summary_data['abstract'] = abstract_match.group(1).strip()
        
        # Extract keywords
        keywords_match = re.search(r'\*\*Keywords:\*\*\s*(.+?)(?=\n\n\*\*|\n\*\*|\Z)', content, re.DOTALL)
        if keywords_match:
            keywords_text = keywords_match.group(1).strip()
            summary_data['keywords'] = [keyword.strip() for keyword in keywords_text.split(',')]
        
        # Extract key findings
        findings_match = re.search(r'\*\*Key Findings:\*\*\s*\n(.*?)(?=\n\n\*\*|\n\*\*|\Z)', content, re.DOTALL)
        if findings_match:
            findings_text = findings_match.group(1).strip()
            findings_lines = [line.strip('- ').strip() for line in findings_text.split('\n') if line.strip() and line.strip().startswith('-')]
            summary_data['key_findings'] = findings_lines
        
        # Extract scientific software
        software_match = re.search(r'\*\*Scientific Software:\*\*\s*\n(.*?)(?=\n\n\*\*|\n\*\*|\Z)', content, re.DOTALL)
        if software_match:
            software_text = software_match.group(1).strip()
            if software_text and not software_text.lower().startswith('none'):
                software_lines = [line.strip('- ').strip() for line in software_text.split('\n') if line.strip() and line.strip().startswith('-')]
                summary_data['scientific_software'] = software_lines
            else:
                summary_data['scientific_software'] = []
        
        # Extract data sources
        sources_match = re.search(r'\*\*Data Sources:\*\*\s*\n(.*?)(?=\n\n\*\*|\n\*\*|\Z)', content, re.DOTALL)
        if sources_match:
            sources_text = sources_match.group(1).strip()
            if sources_text and not sources_text.lower().startswith('none'):
                sources_lines = [line.strip('- ').strip() for line in sources_text.split('\n') if line.strip() and line.strip().startswith('-')]
                summary_data['data_sources'] = sources_lines
            else:
                summary_data['data_sources'] = []
        
        # Extract data sets
        datasets_match = re.search(r'\*\*Data Sets:\*\*\s*\n(.*?)(?=\n\n\*\*|\n\*\*|\Z)', content, re.DOTALL)
        if datasets_match:
            datasets_text = datasets_match.group(1).strip()
            if datasets_text and not datasets_text.lower().startswith('none'):
                datasets_lines = [line.strip('- ').strip() for line in datasets_text.split('\n') if line.strip() and line.strip().startswith('-')]
                summary_data['data_sets'] = datasets_lines
            else:
                summary_data['data_sets'] = []
        
        # Extract data analysis methods
        methods_match = re.search(r'\*\*Data Analysis Methods:\*\*\s*\n(.*?)(?=\n\n\*\*|\n\*\*|\Z)', content, re.DOTALL)
        if methods_match:
            methods_text = methods_match.group(1).strip()
            if methods_text:
                methods_lines = [line.strip('- ').strip() for line in methods_text.split('\n') if line.strip() and line.strip().startswith('-')]
                summary_data['data_analysis_methods'] = methods_lines
            else:
                summary_data['data_analysis_methods'] = []
                
    except Exception as e:
        print(f"Warning: Error parsing formatted content: {e}")
        return None
    
    return summary_data if summary_data else None


def summarize_document(markdown_document_path, 
                       work_dir = work_dir_default, 
                       clear_work_dir = True,
                       summarizer_model = default_agents_llm_model['summarizer'],
                       summarizer_response_formatter_model = default_agents_llm_model['summarizer_response_formatter']):
    
    api_keys = get_api_keys_from_env()
    # load the document from the document_path to markdown file:
    with open(markdown_document_path, 'r') as f:
        markdown_document = f.read()
    
    # Create work directory if it doesn't exist  
    work_dir = Path(work_dir).expanduser().resolve()
    work_dir.mkdir(parents=True, exist_ok=True)

    if clear_work_dir:
        clean_work_dir(work_dir)

    summarizer_config = get_model_config(summarizer_model, api_keys)
    summarizer_response_formatter_config = get_model_config(summarizer_response_formatter_model, api_keys)
    cmbagent = CMBAgent(
        work_dir = work_dir,
        agent_llm_configs = {
                            'summarizer': summarizer_config,
                            'summarizer_response_formatter': summarizer_response_formatter_config,
        },
        api_keys = api_keys,
    )

    start_time = time.time()
    cmbagent.solve(markdown_document,
                    max_rounds=10,
                    initial_agent="summarizer",
                    shared_context = {'current_plan_step_number': 'document_summarizer' }
                    )
    end_time = time.time()
    execution_time_summarization = end_time - start_time

    # Extract the final result from the CMBAgent
    final_context = cmbagent.final_context.data if hasattr(cmbagent.final_context, 'data') else cmbagent.final_context
    chat_result = cmbagent.chat_result
    
    # Extract structured JSON from the chat result
    document_summary = None
    if hasattr(chat_result, 'chat_history'):
        # Look for the summarizer_response_formatter response in the chat history
        # This agent uses a Pydantic BaseModel with structured output
        for message in chat_result.chat_history:
            if isinstance(message, dict) and message.get('name') == 'summarizer_response_formatter':
                # The response_format should contain the structured data
                # Try to extract from tool_calls or parse the formatted content
                if 'tool_calls' in message:
                    # Try to get structured data from tool calls
                    for tool_call in message.get('tool_calls', []):
                        if hasattr(tool_call, 'function') and hasattr(tool_call.function, 'arguments'):
                            try:
                                import json
                                document_summary = json.loads(tool_call.function.arguments)
                                break
                            except:
                                continue
                
                if not document_summary:
                    # Fallback: parse the formatted content back to structured data
                    formatter_content = message.get('content', '')
                    document_summary = _parse_formatted_content(formatter_content)
                break
    
    # Save structured summary to JSON if we have it
    if document_summary and work_dir:
        try:
            import json
            import os
            summary_file = os.path.join(work_dir, 'document_summary.json')
            with open(summary_file, 'w', encoding='utf-8') as f:
                json.dump(document_summary, f, indent=2, ensure_ascii=False)
            print(f"Document summary saved to: {summary_file}")
        except Exception as e:
            print(f"Warning: Could not save document_summary.json: {e}")
    
    # # Return structured output
    # result = {
    #     'chat_history': chat_result.chat_history if hasattr(chat_result, 'chat_history') else [],
    #     'final_context': final_context,
    #     'execution_time': execution_time_summarization,
    #     'work_dir': work_dir,
    #     'document_summary': document_summary,
    # }
    # pretty print the document_summary
    print(json.dumps(document_summary, indent=4))

    # delete codebase and database folders as they are not needed
    shutil.rmtree(os.path.join(work_dir, final_context['codebase_path']), ignore_errors=True)
    shutil.rmtree(os.path.join(work_dir, final_context['database_path']), ignore_errors=True)
    
    cmbagent.display_cost()


    return document_summary


def summarize_documents(folder_path, 
                       work_dir_base = work_dir_default, 
                       clear_work_dir = True,
                       summarizer_model = default_agents_llm_model['summarizer'],
                       summarizer_response_formatter_model = default_agents_llm_model['summarizer_response_formatter'],
                       max_workers = 4,
                       max_depth = 10):
    """
    Process multiple markdown documents in parallel, summarizing each one.
    
    Args:
        folder_path (str): Path to folder containing markdown files
        work_dir_base (str): Base working directory for output
        clear_work_dir (bool): Whether to clear the working directory
        summarizer_model: Model to use for summarizer agent
        summarizer_response_formatter_model: Model to use for formatter agent
        max_workers (int): Maximum number of parallel workers
        max_depth (int): Maximum depth for recursive file search
        
    Returns:
        Dict: Summary of processing results including individual document summaries
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import glob
    import json
    import time
    import os
    
    api_keys = get_api_keys_from_env()
    folder_path = Path(folder_path).resolve()
    
    if not folder_path.exists():
        raise FileNotFoundError(f"Folder not found: {folder_path}")
    if not folder_path.is_dir():
        raise ValueError(f"Path is not a directory: {folder_path}")
        
    print(f"📁 Scanning folder: {folder_path}")
    print(f"🔍 Max depth: {max_depth}")
    print(f"👥 Max workers: {max_workers}")
    
    # Collect all markdown files
    markdown_files = _collect_markdown_files(folder_path, max_depth)
    
    if not markdown_files:
        print("⚠️ No markdown files found in the specified folder.")
        return {
            "processed_files": 0,
            "failed_files": 0,
            "total_files": 0,
            "results": [],
            "folder_path": str(folder_path),
            "work_dir_base": str(work_dir_base)
        }
    
    print(f"📄 Found {len(markdown_files)} markdown files")
    
    # Create base work directory
    work_dir_base = Path(work_dir_base).expanduser().resolve() 
    work_dir_base.mkdir(parents=True, exist_ok=True)
    
    # Initialize results
    results = {
        "processed_files": 0,
        "failed_files": 0,
        "total_files": len(markdown_files),
        "results": [],
        "folder_path": str(folder_path),
        "work_dir_base": str(work_dir_base)
    }
    
    start_time = time.time()

    if clear_work_dir:
        clean_work_dir(work_dir_base)
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks
        future_to_markdown = {
            executor.submit(
                _process_single_markdown_with_error_handling,
                markdown_path,
                i + 1,  # 1-indexed
                work_dir_base,
                clear_work_dir,
                summarizer_model,
                summarizer_response_formatter_model
            ): (markdown_path, i + 1) for i, markdown_path in enumerate(markdown_files)
        }
        
        # Process completed tasks
        for future in as_completed(future_to_markdown):
            markdown_path, index = future_to_markdown[future]
            
            try:
                result = future.result()
                if result.get("success", False):
                    results["processed_files"] += 1
                    print(f"✓ Processed [{index:02d}]: {Path(markdown_path).name}")
                else:
                    results["failed_files"] += 1
                    print(f"✗ Failed [{index:02d}]: {Path(markdown_path).name} - {result.get('error', 'Unknown error')}")
                
                results["results"].append(result)
            except Exception as e:
                results["failed_files"] += 1
                print(f"✗ Failed [{index:02d}]: {Path(markdown_path).name} - {str(e)}")
                results["results"].append({
                    "markdown_path": str(markdown_path),
                    "index": index,
                    "success": False,
                    "error": str(e)
                })
    
    end_time = time.time()
    total_time = end_time - start_time
    
    print(f"\n📋 Processing complete:")
    print(f"  Successfully processed: {results['processed_files']} files")
    print(f"  Failed: {results['failed_files']} files") 
    print(f"  Total time: {total_time:.2f} seconds")
    print(f"  Output directory: {results['work_dir_base']}")
    
    # Save overall summary
    summary_file = work_dir_base / "processing_summary.json"
    try:
        results["processing_time"] = total_time
        results["timestamp"] = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
        
        with open(summary_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"📄 Overall summary saved to: {summary_file}")
    except Exception as e:
        print(f"⚠️ Warning: Could not save overall summary: {e}")
    
    return results


def _collect_markdown_files(folder_path: Path, max_depth: int = 10) -> List[str]:
    """Collect all markdown files from the folder and subfolders."""
    markdown_files = []
    
    # Use glob to find all markdown files recursively
    for ext in ['*.md', '*.markdown']:
        pattern = str(folder_path / "**" / ext)
        markdown_files.extend(glob.glob(pattern, recursive=True))
    
    # Filter by depth if needed
    if max_depth < float('inf'):
        filtered_files = []
        for markdown_file in markdown_files:
            relative_path = Path(markdown_file).relative_to(folder_path)
            depth = len(relative_path.parts) - 1  # -1 because the file itself is not a directory
            if depth <= max_depth:
                filtered_files.append(markdown_file)
        markdown_files = filtered_files
    
    return sorted(markdown_files)


def _process_single_markdown_with_error_handling(markdown_path: str,
                                               index: int,
                                               work_dir_base: Path,
                                               clear_work_dir: bool,
                                               summarizer_model: str,
                                               summarizer_response_formatter_model: str) -> Dict[str, Any]:
    """Process a single markdown file with error handling."""
    try:
        # Create indexed work directory for this document
        work_dir = work_dir_base / f"doc_{index:03d}_{Path(markdown_path).stem}"
        
        # Extract arXiv ID from filename if possible
        import re
        filename = Path(markdown_path).stem
        arxiv_id_pattern = r'([0-9]+\.[0-9]+(?:v[0-9]+)?)'
        arxiv_match = re.search(arxiv_id_pattern, filename)
        arxiv_id = arxiv_match.group(1) if arxiv_match else None
        
        # time the summarize_document function
        start_time = time.time()

        # Call the individual summarize_document function
        summary = summarize_document(
            markdown_document_path=markdown_path,
            work_dir=work_dir,
            clear_work_dir=clear_work_dir,
            summarizer_model=summarizer_model,
            summarizer_response_formatter_model=summarizer_response_formatter_model
        )
        end_time = time.time()
        execution_time_summarization = end_time - start_time
        print(f"Execution time summarization: {execution_time_summarization}")

        # save the timing report
        timing_report = {
            "execution_time_summarization": execution_time_summarization,
            "arxiv_id": arxiv_id
        }
        timing_path = os.path.join(work_dir, "time/timing_report_summarization.json")
        with open(timing_path, 'w') as f:
            json.dump(timing_report, f, indent=2)
        print(f"Timing report saved to {timing_path}")

        return {
            "markdown_path": str(markdown_path),
            "index": index,
            "work_dir": str(work_dir),
            "success": True,
            "document_summary": summary,
            "filename": Path(markdown_path).name,
            "arxiv_id": arxiv_id
        }
        
    except Exception as e:
        # Extract arXiv ID even in error case
        import re
        filename = Path(markdown_path).stem
        arxiv_id_pattern = r'([0-9]+\.[0-9]+(?:v[0-9]+)?)'
        arxiv_match = re.search(arxiv_id_pattern, filename)
        arxiv_id = arxiv_match.group(1) if arxiv_match else None
        
        return {
            "markdown_path": str(markdown_path),
            "index": index,
            "success": False,
            "error": str(e),
            "filename": Path(markdown_path).name,
            "arxiv_id": arxiv_id
        }


def control(
            task,
            plan = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'plans', 'idea_plan.json'),
            max_rounds = 100,
            max_plan_steps = 3,
            n_plan_reviews = 1,
            plan_instructions = '',
            engineer_instructions = '',
            researcher_instructions = '',
            hardware_constraints = '',
            max_n_attempts = 3,
            planner_model = default_agents_llm_model['planner'],
            plan_reviewer_model = default_agents_llm_model['plan_reviewer'],
            engineer_model = default_agents_llm_model['engineer'],
            researcher_model = default_agents_llm_model['researcher'],
            idea_maker_model = default_agents_llm_model['idea_maker'],
            idea_hater_model = default_agents_llm_model['idea_hater'],
            plot_judge_model = default_agents_llm_model['plot_judge'],
            work_dir = work_dir_default,
            clear_work_dir = True,
            api_keys = None,
            ):
    
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
    plot_judge_config = get_model_config(plot_judge_model, api_keys)        
    control_dir = Path(work_dir).expanduser().resolve() / "control"
    control_dir.mkdir(parents=True, exist_ok=True)

    start_time = time.time()
    cmbagent = CMBAgent(
        work_dir = control_dir,
        agent_llm_configs = {
                            'engineer': engineer_config,
                            'researcher': researcher_config,
                            'idea_maker': idea_maker_config,
                            'idea_hater': idea_hater_config,
                            'plot_judge': plot_judge_config,
        },
        clear_work_dir = clear_work_dir,
        api_keys = api_keys
        )
    
    end_time = time.time()
    initialization_time_control = end_time - start_time
    
    start_time = time.time()    
    cmbagent.solve(task,
                    max_rounds=max_rounds,
                    initial_agent="control",
                    shared_context = context
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
    time_full_path = os.path.join(results['final_context']['work_dir'],'time')
    for folder in [database_full_path, codebase_full_path, time_full_path]:
        if not os.listdir(folder):
            os.rmdir(folder)

    return results

def one_shot(
            task,
            max_rounds = 50,
            max_n_attempts = 3,
            engineer_model = default_agents_llm_model['engineer'],
            researcher_model = default_agents_llm_model['researcher'],
            plot_judge_model = default_agents_llm_model['plot_judge'],
            camb_context_model = default_agents_llm_model['camb_context'],
            default_llm_model = default_llm_model_default,
            default_formatter_model = default_formatter_model_default,
            researcher_filename = shared_context_default['researcher_filename'],
            agent = 'engineer',
            work_dir = work_dir_default,
            api_keys = None,
            clear_work_dir = False,
            evaluate_plots = False,
            max_n_plot_evals = 1,
            inject_wrong_plot: bool | str = False,
            ):
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
        mode = "one_shot",
        work_dir = work_dir,
        agent_llm_configs = {
                            'engineer': engineer_config,
                            'researcher': researcher_config,
                            'plot_judge': plot_judge_config,
                            'camb_context': camb_context_config,    
        },
        clear_work_dir = clear_work_dir,
        api_keys = api_keys,

        default_llm_model = default_llm_model,
        default_formatter_model = default_formatter_model,
        )
        
    end_time = time.time()
    initialization_time = end_time - start_time

    start_time = time.time()

    shared_context = {'max_n_attempts': max_n_attempts, 'evaluate_plots': evaluate_plots, 'max_n_plot_evals': max_n_plot_evals, 'inject_wrong_plot': inject_wrong_plot}

    if agent == 'camb_context':

        # Fetch the file (30-second safety timeout)
        resp = requests.get(camb_context_url, timeout=30) # use something different different... debug this lines
        resp.raise_for_status()           # Raises an HTTPError for non-200 codes
        camb_context = resp.text          # Whole document as one long string

        shared_context["camb_context"] = camb_context


    if agent == 'classy_context':

        # Fetch the file (30-second safety timeout)
        resp = requests.get(classy_context_url, timeout=30)
        resp.raise_for_status()           # Raises an HTTPError for non-200 codes
        classy_context = resp.text          # Whole document as one long string

        shared_context["classy_context"] = classy_context


    if researcher_filename is not None: 
        shared_context["researcher_filename"] = researcher_filename

    # print(f"shared_context: {shared_context}")
    # import sys
    # sys.exit()

    cmbagent.solve(task,
                    max_rounds=max_rounds,
                    initial_agent=agent,
                    mode = "one_shot",
                    shared_context = shared_context
                    )
    
    end_time = time.time()
    execution_time = end_time - start_time
    
    # Create a dummy groupchat attribute if it doesn't exist
    # print('creating groupchat for cost display...')
    if not hasattr(cmbagent, 'groupchat'):
        Dummy = type('Dummy', (object,), {'new_conversable_agents': []})
        cmbagent.groupchat = Dummy()
    # print('groupchat created for cost display')
    # Now call display_cost without triggering the AttributeError
    # print('displaying cost...')
    cmbagent.display_cost()
    # print('cost displayed')

    results = {'chat_history': cmbagent.chat_result.chat_history,
               'final_context': cmbagent.final_context,
               'engineer':cmbagent.get_agent_object_from_name('engineer'),
               'engineer_response_formatter':cmbagent.get_agent_object_from_name('engineer_response_formatter'),
               'researcher':cmbagent.get_agent_object_from_name('researcher'),
               'researcher_response_formatter':cmbagent.get_agent_object_from_name('researcher_response_formatter'),
               'plot_judge':cmbagent.get_agent_object_from_name('plot_judge'),
               'plot_debugger':cmbagent.get_agent_object_from_name('plot_debugger')}
    
    
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

    ## delete empty folders during control
    database_full_path = os.path.join(results['final_context']['work_dir'], results['final_context']['database_path'])
    codebase_full_path = os.path.join(results['final_context']['work_dir'], results['final_context']['codebase_path'])
    time_full_path = os.path.join(results['final_context']['work_dir'],'time')
    for folder in [database_full_path, codebase_full_path, time_full_path]:
        if not os.listdir(folder):
            os.rmdir(folder)

    return results

def human_in_the_loop(task,
         work_dir = work_dir_default,
         max_rounds = 50,
         max_n_attempts = 3,
         engineer_model = 'gpt-4o-2024-11-20',
         researcher_model = 'gpt-4o-2024-11-20',
         agent = 'engineer',
         api_keys = None,
         ):

    ## control
    start_time = time.time()

    if api_keys is None:
        api_keys = get_api_keys_from_env()

    engineer_config = get_model_config(engineer_model, api_keys)
    researcher_config = get_model_config(researcher_model, api_keys)

    cmbagent = CMBAgent(
        work_dir = work_dir,
        agent_llm_configs = {
                            'engineer': engineer_config,
                            'researcher': researcher_config,
        },
        mode = "chat",
        chat_agent = agent,
        api_keys = api_keys
        )
        
    end_time = time.time()
    initialization_time = end_time - start_time

    start_time = time.time()

    cmbagent.solve(task,
                    max_rounds=max_rounds,
                    initial_agent=agent,
                    shared_context = {'max_n_attempts': max_n_attempts},
                    mode = "chat")
    
    end_time = time.time()
    execution_time = end_time - start_time

    results = {'chat_history': cmbagent.chat_result.chat_history,
               'final_context': cmbagent.final_context,
               'engineer':cmbagent.get_agent_object_from_name('engineer'),
               'engineer_nest':cmbagent.get_agent_object_from_name('engineer_nest'),
               'engineer_response_formatter':cmbagent.get_agent_object_from_name('engineer_response_formatter'),
               'researcher':cmbagent.get_agent_object_from_name('researcher'),
               'researcher_response_formatter':cmbagent.get_agent_object_from_name('researcher_response_formatter')}
    
    results['initialization_time'] = initialization_time
    results['execution_time'] = execution_time

    if not hasattr(cmbagent, 'groupchat'):
        Dummy = type('Dummy', (object,), {'new_conversable_agents': []})
        cmbagent.groupchat = Dummy()
    # print('groupchat created for cost display')
    # Now call display_cost without triggering the AttributeError
    # print('displaying cost...')
    cmbagent.display_cost()
    # print('cost displayed')

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

def get_keywords(input_text: str, n_keywords: int = 5, 
                 work_dir = work_dir_default, 
                 api_keys = get_api_keys_from_env(),
                 kw_type = 'unesco'):
    """
    Get keywords from input text.

    Args:
        input_text (str): Text to extract keywords from
        n_keywords (int, optional): Number of keywords to extract. Defaults to 5.
        **kwargs: Additional keyword arguments

    Returns:
        dict: Dictionary of keywords
    """
    if kw_type == 'aas':
        return get_aas_keywords(input_text, n_keywords, work_dir, api_keys)
    elif kw_type == 'unesco':

        aggregated_keywords = []

        ukw = UnescoKeywords(unesco_taxonomy_path)
        keywords_string = ', '.join(ukw.get_unesco_level1_names())
        n_keywords_level1  = ukw.n_keywords_level1
        domains = get_keywords_from_string(input_text, keywords_string, n_keywords_level1, work_dir, api_keys)

        print('domains:')
        print(domains)
        domains.append('MATHEMATICS') if not 'MATHEMATICS' in domains else None
        aggregated_keywords.extend(domains)

        for domain in domains:
            print('inside domain: ', domain)
            if '&' in domain:
                domain = domain.replace('&', '\\&')
            keywords_string = ', '.join(ukw.get_unesco_level2_names(domain))
            n_keywords_level2 = ukw.n_keywords_level2
            sub_fields = get_keywords_from_string(input_text, keywords_string, n_keywords_level2, work_dir, api_keys)

            print('sub_fields:')
            print(sub_fields)
            aggregated_keywords.extend(sub_fields)

            for sub_field in sub_fields:
                print('inside sub_field: ', sub_field)
                keywords_string = ', '.join(ukw.get_unesco_level3_names(sub_field))
                n_keywords_level3 = ukw.n_keywords_level3
                specific_areas = get_keywords_from_string(input_text, keywords_string, n_keywords_level3, work_dir, api_keys)
                print('specific_areas:')
                print(specific_areas)
                aggregated_keywords.extend(specific_areas)


        aggregated_keywords = list(set(aggregated_keywords))
        keywords_string = ', '.join(aggregated_keywords)
        keywords = get_keywords_from_string(input_text, keywords_string, n_keywords, work_dir, api_keys)

        print('keywords in unesco:')
        print(keywords)
        return keywords
    elif kw_type == 'aaai':
        return get_keywords_from_aaai(input_text, n_keywords, work_dir, api_keys)

        
        # return aas_keywords




def get_keywords_from_aaai(input_text, n_keywords=6, work_dir=work_dir_default, api_keys=get_api_keys_from_env()):
    start_time = time.time()
    cmbagent = CMBAgent(work_dir = work_dir, api_keys = api_keys)
    end_time = time.time()
    initialization_time = end_time - start_time

    PROMPT = f"""
    {input_text}
    """
    start_time = time.time()

    aaai_keywords = AaaiKeywords(aaai_keywords_path)

    keywords_string = aaai_keywords.aaai_keywords_string

    
    cmbagent.solve(task="Find the relevant keywords in the provided list",
            max_rounds=2,
            initial_agent='aaai_keywords_finder',
            mode = "one_shot",
            shared_context={
            'text_input_for_AAS_keyword_finder': PROMPT,
            'AAS_keywords_string': keywords_string,
            'N_AAS_keywords': n_keywords,
                            }
            )
    end_time = time.time()
    execution_time = end_time - start_time
    # aas_keywords = cmbagent.final_context['aas_keywords'] ## here you get the dict with urls

    if not hasattr(cmbagent, 'groupchat'):
        Dummy = type('Dummy', (object,), {'new_conversable_agents': []})
        cmbagent.groupchat = Dummy()
    # print('groupchat created for cost display')
    # Now call display_cost without triggering the AttributeError
    # print('displaying cost...')
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

    # grab last user message with role "user"
    user_msg = next(
        (msg["content"] for msg in cmbagent.chat_result.chat_history if msg.get("role") == "user"),
        ""
    )

    # extract lines starting with a dash
    keywords = [line.lstrip("-").strip() for line in user_msg.splitlines() if line.startswith("-")]
    return keywords


def get_keywords_from_string(input_text,keywords_string, n_keywords, work_dir, api_keys):
    start_time = time.time()
    cmbagent = CMBAgent(work_dir = work_dir, api_keys = api_keys)
    end_time = time.time()
    initialization_time = end_time - start_time

    PROMPT = f"""
    {input_text}
    """
    start_time = time.time()

    
    cmbagent.solve(task="Find the relevant keywords in the provided list",
            max_rounds=2,
            initial_agent='list_keywords_finder',
            mode = "one_shot",
            shared_context={
            'text_input_for_AAS_keyword_finder': PROMPT,
            'AAS_keywords_string': keywords_string,
            'N_AAS_keywords': n_keywords,
                            }
            )
    end_time = time.time()
    execution_time = end_time - start_time
    # aas_keywords = cmbagent.final_context['aas_keywords'] ## here you get the dict with urls

    if not hasattr(cmbagent, 'groupchat'):
        Dummy = type('Dummy', (object,), {'new_conversable_agents': []})
        cmbagent.groupchat = Dummy()
    # print('groupchat created for cost display')
    # Now call display_cost without triggering the AttributeError
    # print('displaying cost...')
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

    # grab last user message with role "user"
    user_msg = next(
        (msg["content"] for msg in cmbagent.chat_result.chat_history if msg.get("role") == "user"),
        ""
    )

    # extract lines starting with a dash
    keywords = [line.lstrip("-").strip() for line in user_msg.splitlines() if line.startswith("-")]
    return keywords

def get_aas_keywords(input_text: str, n_keywords: int = 5, 
                 work_dir = work_dir_default, 
                 api_keys = get_api_keys_from_env()):
    """
    Get keywords from input text.

    Args:
        input_text (str): Text to extract keywords from
        n_keywords (int, optional): Number of keywords to extract. Defaults to 5.
        **kwargs: Additional keyword arguments

    Returns:
        dict: Dictionary of keywords
    """
    start_time = time.time()
    cmbagent = CMBAgent(work_dir = work_dir, api_keys = api_keys)
    end_time = time.time()
    initialization_time = end_time - start_time

    PROMPT = f"""
    {input_text}
    """
    start_time = time.time()
    cmbagent.solve(task="Find the relevant AAS keywords",
            max_rounds=50,
            initial_agent='aas_keyword_finder',
            mode = "one_shot",
            shared_context={
            'text_input_for_AAS_keyword_finder': PROMPT,
            'AAS_keywords_string': AAS_keywords_string,
            'N_AAS_keywords': n_keywords,
                            }
            )
    end_time = time.time()
    execution_time = end_time - start_time
    aas_keywords = cmbagent.final_context['aas_keywords'] ## here you get the dict with urls

    if not hasattr(cmbagent, 'groupchat'):
        Dummy = type('Dummy', (object,), {'new_conversable_agents': []})
        cmbagent.groupchat = Dummy()
    # print('groupchat created for cost display')
    # Now call display_cost without triggering the AttributeError
    # print('displaying cost...')
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

    print('aas_keywords: ', aas_keywords)
    
    return aas_keywords



def preprocess_task(text: str, 
                   work_dir: str = work_dir_default,
                   clear_work_dir: bool = True,
                   max_workers: int = 4,
                   max_depth: int = 10,
                   summarizer_model: str = default_agents_llm_model['summarizer'],
                   summarizer_response_formatter_model: str = default_agents_llm_model['summarizer_response_formatter'],
                   skip_arxiv_download: bool = False,
                   skip_ocr: bool = False,
                   skip_summarization: bool = False) -> str:
    """
    Preprocess a task description by:
    1. Extracting arXiv URLs and downloading PDFs
    2. OCRing PDFs to markdown
    3. Summarizing the papers
    4. Appending contextual information to the original text
    
    Args:
        text: The input task description text containing arXiv URLs
        work_dir: Working directory for processing files
        clear_work_dir: Whether to clear the work directory before starting
        max_workers: Number of parallel workers for processing
        max_depth: Maximum directory depth for file searching
        skip_arxiv_download: Skip the arXiv download step
        skip_ocr: Skip the OCR step
        skip_summarization: Skip the summarization step
        
    Returns:
        str: The original text with appended "Contextual Information and References" section
    """
    import os
    from .arxiv_downloader import arxiv_filter
    from .ocr import process_folder
    
    print(f"🔄 Starting task preprocessing...")
    print(f"📁 Work directory: {work_dir}")

    if clear_work_dir:
        clean_work_dir(work_dir)
    
    # Step 1: Extract arXiv URLs and download PDFs
    arxiv_results = None
    if not skip_arxiv_download:
        print(f"📥 Step 1: Downloading arXiv papers...")
        try:
            arxiv_results = arxiv_filter(text, work_dir=work_dir)
            print(f"✅ Downloaded {arxiv_results['downloads_successful']} papers")
            print(f"📋 Total papers available: {arxiv_results['downloads_successful'] + arxiv_results['downloads_skipped']} (including previously downloaded)")
            
            if arxiv_results['downloads_successful'] + arxiv_results['downloads_skipped'] == 0:
                print("ℹ️ No arXiv papers found or available, skipping processing steps")
                return text
        except Exception as e:
            print(f"❌ Error downloading arXiv papers: {e}")
            return text
    
    # Get the docs folder path where PDFs were downloaded
    docs_folder = os.path.join(work_dir, "docs")
    if not os.path.exists(docs_folder):
        print("ℹ️ No docs folder found, returning original text")
        return text
    
    # Step 2: OCR PDFs to markdown
    ocr_results = None
    if not skip_ocr:
        print(f"🔍 Step 2: Converting PDFs to markdown...")
        try:
            ocr_results = process_folder(
                folder_path=docs_folder,
                save_markdown=True,
                save_json=False,  # We don't need JSON for summarization
                save_text=False,
                max_depth=max_depth,
                max_workers=max_workers,
                work_dir=work_dir
            )
            print(f"✅ OCR processed {ocr_results.get('processed_files', 0)} files")
            if ocr_results.get('processed_files', 0) == 0:
                print("ℹ️ No PDF files found for OCR, returning original text")
                return text
        except Exception as e:
            print(f"❌ Error during OCR processing: {e}")
            return text
    
    # Find the markdown output directory from OCR
    docs_processed_folder = docs_folder + "_processed"
    if not os.path.exists(docs_processed_folder):
        print(f"ℹ️ No processed markdown folder found at {docs_processed_folder}, returning original text")
        return text
    
    # Step 3: Summarize the markdown documents
    summary_results = None
    if not skip_summarization:
        print(f"📝 Step 3: Summarizing papers...")
        try:
            # Create summaries directory in the main work directory
            summaries_dir = os.path.join(work_dir, "summaries")
            summary_results = summarize_documents(
                folder_path=docs_processed_folder,
                work_dir_base=summaries_dir,
                clear_work_dir=clear_work_dir,
                max_workers=max_workers,
                max_depth=max_depth,
                summarizer_model=summarizer_model,
                summarizer_response_formatter_model=summarizer_response_formatter_model
            )
            print(f"✅ Summarized {summary_results.get('processed_files', 0)} documents")
            
            if summary_results.get('processed_files', 0) == 0:
                print("ℹ️ No documents were summarized, returning original text")
                return text
            
            # Step 4: Collect all summaries and format the contextual information
            print(f"📋 Step 4: Formatting contextual information...")
            contextual_info = []
            
            for result in summary_results.get('results', []):
                if result.get('success', False) and 'document_summary' in result:
                    summary = result['document_summary']
                    arxiv_id = result.get('arxiv_id')
                    
                    # Format each summary
                    title = summary.get('title', 'Unknown Title')
                    authors = summary.get('authors', [])
                    authors_str = ', '.join(authors) if authors else 'Unknown Authors'
                    date = summary.get('date', 'Unknown Date')
                    abstract = summary.get('abstract', 'No abstract available')
                    keywords = summary.get('keywords', [])
                    keywords_str = ', '.join(keywords) if keywords else 'No keywords'
                    key_findings = summary.get('key_findings', [])
                    
                    # Add arXiv ID if available
                    arxiv_info = f" (arXiv:{arxiv_id})" if arxiv_id else ""
                    
                    paper_info = f"""
**{title}{arxiv_info}**
- Authors: {authors_str}
- Date: {date}
- Keywords: {keywords_str}
- Abstract: {abstract}"""
                    
                    if key_findings:
                        paper_info += "\n- Key Findings:"
                        for finding in key_findings:
                            paper_info += f"\n  • {finding}"
                    
                    contextual_info.append(paper_info)
            
            # Step 5: Append the contextual information to the original text
            if contextual_info:
                footer = "\n\n## Contextual Information and References\n"
                footer += "\n".join(contextual_info)
                
                enhanced_text = text + footer
                
                # Save enhanced text to enhanced_input.md
                enhanced_input_path = os.path.join(work_dir, "enhanced_input.md")
                try:
                    with open(enhanced_input_path, 'w', encoding='utf-8') as f:
                        f.write(enhanced_text)
                    print(f"💾 Enhanced input saved to: {enhanced_input_path}")
                except Exception as e:
                    print(f"⚠️ Warning: Could not save enhanced input: {e}")
                
                print(f"✅ Task preprocessing completed successfully!")
                print(f"📄 Added contextual information from {len(contextual_info)} papers")
                return enhanced_text
            else:
                print("ℹ️ No valid summaries found, returning original text")
                return text
        except Exception as e:
            print(f"❌ Error during summarization: {e}")
            return text
    
    return text