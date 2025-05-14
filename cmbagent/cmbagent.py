import os
import logging
import importlib
import requests
import autogen 
import ast
import json
import sys
import pandas as pd
import copy
import datetime
from typing import Any, Dict
from IPython.display import display
from pathlib import Path
from .agents.planner_response_formatter.planner_response_formatter import save_final_plan
from collections import defaultdict
from .utils import work_dir as work_dir_default
from .utils import OpenAI,Image
from .utils import default_llm_model as default_llm_model_default
from .utils import (path_to_assistants,path_to_apis,
                    default_top_p,default_temperature,default_max_round,default_llm_config_list,default_agent_llm_configs,
                    default_agents_llm_model)
from pprint import pprint
from .rag_utils import import_rag_agents, push_vector_stores
from .utils import path_to_agents, update_yaml_preserving_format
from .hand_offs import register_all_hand_offs
from .functions import register_functions_to_agents
import time
from .utils import get_model_config
from .utils import AAS_keywords_string
from autogen.agentchat.group import ContextVariables
from autogen import ConversableAgent


from .data_retriever import setup_cmbagent_data



from autogen.agentchat.group.patterns import (
    DefaultPattern,
    ManualPattern,
    AutoPattern,
    RandomPattern,
    RoundRobinPattern,
)


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


from pydantic import BaseModel
from autogen import cmbagent_debug
from autogen.agentchat import initiate_group_chat
from cmbagent.context import shared_context as shared_context_default
from sys import exit
import shutil




class CMBAgent:

    logging.disable(logging.CRITICAL)
    cmbagent_debug = autogen.cmbagent_debug



    def __init__(self,
                 cache_seed=42,
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
                 default_llm_config_list = default_llm_config_list,
                 agent_llm_configs = default_agent_llm_configs,
                 agent_type = 'swarm',# None,# 'swarm',
                 shared_context = shared_context_default,
                 work_dir = work_dir_default,
                 clear_work_dir = True,
                 mode = "planning_and_control", # can be "one_shot" , "chat" or "planning_and_control" (default is planning and control), or "planning_and_control_context_carryover"
                 chat_agent = None,
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
            print(f"Warning: default_llm_model is set to {default_llm_model} in cmbagent.py")
            default_llm_config_list = [get_model_config(default_llm_model)]



        self.kwargs = kwargs

        self.skip_executor = skip_executor

        self.skip_rag_agents = skip_rag_agents

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

        self.work_dir = work_dir
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

        self.init_agents(agent_llm_configs=self.agent_llm_configs) # initialize agents

        if cmbagent_debug:
            print("\n\n All agents instantiated!!!\n\n")

        if cmbagent_debug:  
            print("\n\n Checking assistants...\n\n")



        if not self.skip_rag_agents:
            setup_cmbagent_data()

            self.check_assistants(reset_assistant=reset_assistant) # check if assistants exist

            if cmbagent_debug:
                print("\n\n Assistants checked!!!\n\n")
                # sys.exit()


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
        from collections import defaultdict
        import pandas as pd
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

                if name in cost_dict["Agent"]:
                    i = cost_dict["Agent"].index(name)
                    cost_dict["Cost ($)"][i]              += summed_cost
                    cost_dict["Prompt Tokens"][i]     += summed_prompt
                    cost_dict["Completion Tokens"][i] += summed_comp
                    cost_dict["Total Tokens"][i]      += summed_total
                else:
                    cost_dict["Agent"].append(name)
                    cost_dict["Cost ($)"].append(summed_cost)
                    cost_dict["Prompt Tokens"].append(summed_prompt)
                    cost_dict["Completion Tokens"].append(summed_comp)
                    cost_dict["Total Tokens"].append(summed_total)

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

        # --- Save cost data as JSON ------------------------------------------------
        # Convert DataFrame to dict for JSON serialization
        cost_data = df.to_dict(orient='records')
        
        # Add timestamp
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Save to JSON file in workdir
        if name_append is not None:
            json_path = os.path.join(self.work_dir, f"cost_report_{name_append}_{timestamp}.json")
        else:
            json_path = os.path.join(self.work_dir, f"cost_report_{timestamp}.json")
        with open(json_path, 'w') as f:
            json.dump(cost_data, f, indent=2)
            
        print(f"\nCost report data saved to: {json_path}\n")
        
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
        

        self.clear_cache() ## obsolete
        if self.clear_work_dir_bool:
            self.clear_work_dir()

        # Define full paths
        database_full_path = os.path.join(self.work_dir, this_shared_context.get("database_path", "data"))
        codebase_full_path = os.path.join(self.work_dir, this_shared_context.get("codebase_path", "codebase"))
        
        # Create directories if they don't exist
        os.makedirs(database_full_path, exist_ok=True)
        os.makedirs(codebase_full_path, exist_ok=True)

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
                group_manager_args = {"llm_config": self.llm_config},
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

    def init_agents(self,agent_llm_configs=None):

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
            # import sys; sys.exit()



        if cmbagent_debug:
            print('self.agent_classes after update: ')
            print()
            for agent_class, value in self.agent_classes.items():
                print(f'{agent_class}: {value}')
                print()
            # sys.exit()

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
            # sys.exit()

        # remove agents that are not set to be skipped
        if self.skip_memory:
            # self.agent_classes.pop('memory')
            self.agent_classes.pop('summarizer')
        
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
            # sys.exit()

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

                if "reasoning_effort" in llm_config['config_list'][0]:
                    llm_config.pop('temperature')
                    llm_config.pop('top_p')

                if llm_config['config_list'][0]['api_type'] == 'google':
                    llm_config.pop('top_p') 
                
                if cmbagent_debug:
                    print('in cmbagent.py: found agent_llm_configs for: ', agent_name)
                    print('in cmbagent.py: llm_config updated to: ', llm_config)
            else:
                llm_config = copy.deepcopy(self.llm_config)

            if cmbagent_debug:
                print('in cmbagent.py BEFORE agent_instance: llm_config: ', llm_config)

            agent_instance = agent_class(llm_config=llm_config,agent_type=self.agent_type, work_dir=self.work_dir)

            # sys.exit()

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

        # sys.exit()


        if self.verbose or cmbagent_debug:

            print("Using following agents: ", self.agent_names)
            print("Using following llm for agents: ")
            for agent in self.agents:
                print(f"{agent.name}: {agent.llm_config['config_list'][0]['model']}")
            print()
            # sys.exit()

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
                            print(f"in cmbagent.py check_assistants: Assistant model from openai does not match the requested model. Updating the assistant model.")
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
                                print(f"--> Assistant ID between yaml and openai do not match.")
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
        cache_dir = autogen.oai.client.LEGACY_CACHE_DIR
        if os.path.exists(cache_dir):
            shutil.rmtree(cache_dir)
        # sys.exit()s
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





def planning_and_control_context_carryover(
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
                            default_llm_model = default_llm_model_default,
                            work_dir = work_dir_default
                            ):

    ## planning
    planning_dir = Path(work_dir).expanduser().resolve() / "planning"

    start_time = time.time()
    planner_config = get_model_config(planner_model)
    plan_reviewer_config = get_model_config(plan_reviewer_model)

    
    cmbagent = CMBAgent(work_dir = planning_dir,
                        default_llm_model = default_llm_model,
                        agent_llm_configs = {
                            'planner': planner_config,
                            'plan_reviewer': plan_reviewer_config,
                        })
    end_time = time.time()
    initialization_time_planning = end_time - start_time

    # print("initialization_time_planning: ", initialization_time_planning)
    # import sys 
    # sys.exit()




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
                                    'hardware_constraints': hardware_constraints}
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
    number_of_steps_in_plan = planning_output['number_of_steps_in_plan']
    outfile = save_final_plan(planning_output, planning_dir)
    print(f"\nStructured plan written to {outfile}")
    print(f"\nPlanning took {execution_time_planning:.4f} seconds\n")
    

    ## control

    engineer_config = get_model_config(engineer_model)
    researcher_config = get_model_config(researcher_model)
    idea_maker_config = get_model_config(idea_maker_model)
    idea_hater_config = get_model_config(idea_hater_model)
        
    control_dir = Path(work_dir).expanduser().resolve() / "control"

    current_context = copy.deepcopy(planning_output)
    step_summaries = []  
    # print("in cmbagent.py: current_context before step loop: ", current_context)

    for step in range(1, number_of_steps_in_plan + 1):
        clear_work_dir = True if step == 1 else False
        starter_agent = "control" if step == 1 else "control_starter"
        # print(f"in cmbagent.py: step: {step}/{number_of_steps_in_plan}")

        # print("current_context['previous_steps_execution_summary']: ", current_context['previous_steps_execution_summary'] )


        start_time = time.time()
        cmbagent = CMBAgent(
            work_dir = control_dir,
            clear_work_dir = clear_work_dir,
            default_llm_model = default_llm_model,
            agent_llm_configs = {
                                'engineer': engineer_config,
                                'researcher': researcher_config,
                                'idea_maker': idea_maker_config,
                                'idea_hater': idea_hater_config,
            },
            mode = "planning_and_control_context_carryover"
            )
        

        # print(f"in cmbagent.py: idea_maker_config: {idea_maker_config}")
        # print(f"in cmbagent.py: idea_hater_config: {idea_hater_config}")
        
        end_time = time.time()
        initialization_time_control = end_time - start_time
        
        if step == 1:
            plan_input = load_plan(os.path.join(work_dir, f"planning/final_plan.json"))["sub_tasks"]
            agent_for_step = plan_input[0]['sub_task_agent']
        else:
            agent_for_step = current_context['agent_for_sub_task']
        # print(f"\nin cmbagent.py: agent_for_step: {agent_for_step}")


        parsed_context = copy.deepcopy(current_context)
        # print("xo"*100+"\n\n")
        # print("in cmbagent.py: parsed_context: ", parsed_context)
        # print("xo"*100+"\n\n")
            

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
        timing_path = os.path.join(work_dir, f"timing_report_step_{step}_{timestamp}.json")
        with open(timing_path, 'w') as f:
            json.dump(timing_report, f, indent=2)

        
        # Create a dummy groupchat attribute if it doesn't exist
        if not hasattr(cmbagent, 'groupchat'):
            Dummy = type('Dummy', (object,), {'new_conversable_agents': []})
            cmbagent.groupchat = Dummy()

        # Now call display_cost without triggering the AttributeError
        cmbagent.display_cost(name_append = f"step_{step}")

        # if step == 4:
        #     break


    return results


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
                            work_dir = work_dir_default
                            ):

    ## planning
    planning_dir = Path(work_dir).expanduser().resolve() / "planning"

    start_time = time.time()
    planner_config = get_model_config(planner_model)
    plan_reviewer_config = get_model_config(plan_reviewer_model)
    cmbagent = CMBAgent(work_dir = planning_dir,
                        agent_llm_configs = {
                            'planner': planner_config,
                            'plan_reviewer': plan_reviewer_config,
                        })
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
                                    'hardware_constraints': hardware_constraints}
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
    

    ## control

    engineer_config = get_model_config(engineer_model)
    researcher_config = get_model_config(researcher_model)
    idea_maker_config = get_model_config(idea_maker_model)
    idea_hater_config = get_model_config(idea_hater_model)
        
    control_dir = Path(work_dir).expanduser().resolve() / "control"

    start_time = time.time()
    cmbagent = CMBAgent(
        work_dir = control_dir,
        agent_llm_configs = {
                            'engineer': engineer_config,
                            'researcher': researcher_config,
                            'idea_maker': idea_maker_config,
                            'idea_hater': idea_hater_config,
        })
    

    print(f"in cmbagent.py: idea_maker_config: {idea_maker_config}")
    print(f"in cmbagent.py: idea_hater_config: {idea_hater_config}")
    
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
    timing_path = os.path.join(work_dir, f"timing_report_{timestamp}.json")
    with open(timing_path, 'w') as f:
        json.dump(timing_report, f, indent=2)

    
    # Create a dummy groupchat attribute if it doesn't exist
    if not hasattr(cmbagent, 'groupchat'):
        Dummy = type('Dummy', (object,), {'new_conversable_agents': []})
        cmbagent.groupchat = Dummy()

    # Now call display_cost without triggering the AttributeError
    cmbagent.display_cost()


    return results


def load_plan(plan_path):
    """Load a plan from a JSON file into a dictionary"""
    with open(plan_path, 'r') as f:
        plan_dict = json.load(f)
    
    return plan_dict



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
            work_dir = work_dir_default
                            ):
    
    # check work_dir exists
    if not os.path.exists(work_dir):
        os.makedirs(work_dir)



    planning_input = load_plan(plan)["sub_tasks"]



    # import pprint
    # pprint.pprint(planning_input)


    context = {'final_plan': planning_input,
               "number_of_steps_in_plan": len(planning_input),
               "agent_for_sub_task": planning_input[0]['sub_task_agent'],
               "current_sub_task": planning_input[0]['sub_task'],
               "current_instructions": ''}
    for bullet in planning_input[0]['bullet_points']:
        context["current_instructions"] += f"\t\t- {bullet}\n"


    # pprint.pprint(context)

    # import sys
    # sys.exit()

    ## control

    engineer_config = get_model_config(engineer_model)
    researcher_config = get_model_config(researcher_model)
    idea_maker_config = get_model_config(idea_maker_model)
    idea_hater_config = get_model_config(idea_hater_model)
        
    control_dir = Path(work_dir).expanduser().resolve() / "control"

    start_time = time.time()
    cmbagent = CMBAgent(
        work_dir = control_dir,
        agent_llm_configs = {
                            'engineer': engineer_config,
                            'researcher': researcher_config,
                            'idea_maker': idea_maker_config,
                            'idea_hater': idea_hater_config,
        })
    

    # print(f"in cmbagent.py: idea_maker_config: {idea_maker_config}")
    # print(f"in cmbagent.py: idea_hater_config: {idea_hater_config}")
    
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
    timing_path = os.path.join(work_dir, f"timing_report_{timestamp}.json")
    with open(timing_path, 'w') as f:
        json.dump(timing_report, f, indent=2)

    
    # Create a dummy groupchat attribute if it doesn't exist
    if not hasattr(cmbagent, 'groupchat'):
        Dummy = type('Dummy', (object,), {'new_conversable_agents': []})
        cmbagent.groupchat = Dummy()

    # Now call display_cost without triggering the AttributeError
    cmbagent.display_cost()


    return results









def one_shot(
            task,
            max_rounds = 50,
            max_n_attempts = 3,
            engineer_model = default_agents_llm_model['engineer'],
            researcher_model = default_agents_llm_model['researcher'],
            agent = 'engineer',
            work_dir = work_dir_default
            ):
    start_time = time.time()


    engineer_config = get_model_config(engineer_model)
    researcher_config = get_model_config(researcher_model)
        

    cmbagent = CMBAgent(
        work_dir = work_dir,
        agent_llm_configs = {
                            'engineer': engineer_config,
                            'researcher': researcher_config,
        })
        
    end_time = time.time()
    initialization_time = end_time - start_time


    start_time = time.time()

    cmbagent.solve(task,
                    max_rounds=max_rounds,
                    initial_agent=agent,
                    mode = "one_shot",
                    shared_context = {'max_n_attempts': max_n_attempts}
                    )
    
    end_time = time.time()
    execution_time = end_time - start_time

    results = {'chat_history': cmbagent.chat_result.chat_history,
               'final_context': cmbagent.final_context,
               'engineer':cmbagent.get_agent_object_from_name('engineer'),
               'engineer_response_formatter':cmbagent.get_agent_object_from_name('engineer_response_formatter'),
               'researcher':cmbagent.get_agent_object_from_name('researcher'),
               'researcher_response_formatter':cmbagent.get_agent_object_from_name('researcher_response_formatter')}
    
    
    results['initialization_time'] = initialization_time
    results['execution_time'] = execution_time
    
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

    print("\nTiming report saved to", timing_path)
    print("\nTask took", f"{execution_time:.4f}", "seconds")

    return results




def human_in_the_loop(task,
         work_dir = work_dir_default,
         max_rounds = 50,
         max_n_attempts = 3,
         engineer_model = 'gpt-4o-2024-11-20',
         researcher_model = 'gpt-4o-2024-11-20',
         agent = 'engineer'):

    ## control
    start_time = time.time()

    engineer_config = get_model_config(engineer_model)
    researcher_config = get_model_config(researcher_model)




    cmbagent = CMBAgent(
        work_dir = work_dir,
        agent_llm_configs = {
                            'engineer': engineer_config,
                            'researcher': researcher_config,
        
        },
        mode = "chat",
        chat_agent = agent)
        
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






def get_keywords(input_text: str, n_keywords: int = 5, work_dir = work_dir_default):
    """
    Get AAS keywords from input text using astropilot.

    Args:
        input_text (str): Text to extract keywords from
        n_keywords (int, optional): Number of keywords to extract. Defaults to 5.
        **kwargs: Additional keyword arguments

    Returns:
        dict: Dictionary mapping AAS keywords to their URLs
    """
    start_time = time.time()
    cmbagent = CMBAgent(work_dir = work_dir)
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
    
    return aas_keywords