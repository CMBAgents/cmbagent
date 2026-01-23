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

from .agents.planning.planner_response_formatter.planner_response_formatter import save_final_plan
from .utils import work_dir_default
from .utils import default_llm_model as default_llm_model_default
from .utils import default_formatter_model as default_formatter_model_default
from .utils import clean_llm_config

from .utils import (path_to_apis,path_to_agents, update_yaml_preserving_format, get_model_config,
                    default_top_p, default_temperature, default_max_round,default_llm_config_list, default_agent_llm_configs,
                    default_agents_llm_model, camb_context_url, AAS_keywords_string, get_api_keys_from_env)

from .hand_offs import register_all_hand_offs
from .functions import register_functions_to_agents
from .workflows.one_shot import one_shot
from .workflows.deep_research import deep_research
from .workflows.human_in_the_loop import human_in_the_loop
from .workflows.planning_and_control import planning_and_control
from .workflows.keywords import get_keywords, get_keywords_from_aaai, get_keywords_from_string, get_aas_keywords
from .workflows.control import control, load_plan

from .utils.keywords_utils import UnescoKeywords
from .utils.keywords_utils import AaaiKeywords
from .utils import unesco_taxonomy_path, aaai_keywords_path

def import_agents():
    imported_agents = {}
    for item in os.listdir(path_to_agents):
        # Skip hidden items and pycache
        if item.startswith(".") or item == "__pycache__":
            continue
        item_path = os.path.join(path_to_agents, item)
        if not os.path.isdir(item_path):
            continue

        # Check if this is an agent folder (has .py and .yaml files) or a category folder
        has_py_file = any(f.endswith(".py") and f != "__init__.py" for f in os.listdir(item_path))

        if has_py_file:
            # This is an agent folder directly under agents/ (old structure)
            for filename in os.listdir(item_path):
                if filename.endswith(".py") and filename != "__init__.py" and filename[0] != ".":
                    module_name = filename[:-3]
                    class_name = ''.join([part.capitalize() for part in module_name.split('_')]) + 'Agent'
                    module_path = f"cmbagent.agents.{item}.{module_name}"
                    module = importlib.import_module(module_path)
                    agent_class = getattr(module, class_name)
                    imported_agents[class_name] = {
                        'agent_class': agent_class,
                        'agent_name': module_name,
                    }
        else:
            # This is a category folder (new structure)
            for agent_folder in os.listdir(item_path):
                agent_folder_path = os.path.join(item_path, agent_folder)
                if os.path.isdir(agent_folder_path) and not agent_folder.startswith(".") and agent_folder != "__pycache__":
                    for filename in os.listdir(agent_folder_path):
                        if filename.endswith(".py") and filename != "__init__.py" and filename[0] != ".":
                            module_name = filename[:-3]
                            class_name = ''.join([part.capitalize() for part in module_name.split('_')]) + 'Agent'
                            module_path = f"cmbagent.agents.{item}.{agent_folder}.{module_name}"
                            module = importlib.import_module(module_path)
                            agent_class = getattr(module, class_name)
                            imported_agents[class_name] = {
                                'agent_class': agent_class,
                                'agent_name': module_name,
                            }
    return imported_agents

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
                 select_speaker_prompt = None,
                 select_speaker_message = None,
                 intro_message = None,
                 set_allowed_transitions = None,
                 skip_executor = False,
                 skip_memory = True,
                 default_llm_model = default_llm_model_default,
                 default_formatter_model = default_formatter_model_default,
                 default_llm_config_list = default_llm_config_list,
                 agent_llm_configs = default_agent_llm_configs,
                 agent_type = 'swarm',# None,# 'swarm',
                 shared_context = shared_context_default,
                 work_dir = work_dir_default,
                 clear_work_dir = True,
                 mode = "planning_and_control", # can be "one_shot", "human_in_the_loop", or "planning_and_control" (default is planning and control), or "deep_research"
                 chat_agent = None,
                 api_keys = None,
                 **kwargs):
        """
        Initialize the CMBAgent.

        Args:
            cache_seed (int, optional): Seed for caching. Defaults to 42.
            temperature (float, optional): Temperature for LLM sampling. Defaults to 0.
            timeout (int, optional): Timeout for LLM requests in seconds. Defaults to 1200.
            max_round (int, optional): Maximum number of conversation rounds. Defaults to 50. If too small, the conversation stops.
            llm_api_key (str, optional): API key for LLM. If None, uses the key from the config file.

            **kwargs: Additional keyword arguments.

        Attributes:
            kwargs (dict): Additional keyword arguments.
            work_dir (str): Working directory for output.
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

        self.set_allowed_transitions = set_allowed_transitions

        self.logger = logging.getLogger(__name__)

        self.skip_memory = skip_memory

        self.results = {}

        self.mode = mode
        self.chat_agent = chat_agent

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
              initial_agent='planner',
              shared_context=None,
              mode = "default", # can be "one_shot" or "default" (default is planning and control)
              step = None,
              max_rounds=10):
        """
        Orchestrate a multi-agent group chat to solve a given task.
        
        This method sets up the work environment, initializes agents, creates an AutoPattern
        for agent coordination, and initiates a group chat where agents collaborate to solve
        the specified task. The results are stored in instance variables rather than returned.
        
        Parameters
        ----------
        task : str
            The main task description to be solved by the agent system. This will be set as
            the 'main_task' and 'improved_main_task' in the shared context.
            
        initial_agent : str, optional
            The name of the agent to start the conversation with. Defaults to 'planner'.
            Must be a valid agent name that exists in the agent system.
            
        shared_context : dict, optional
            Additional context variables to merge into the shared context. These will be
            combined with the default shared context and any mode-specific context. If None,
            only the default and mode-specific contexts are used.
            
        mode : str, optional
            The execution mode for the solve operation. Valid options are:
            - "default": Planning and control mode (default). Uses the full planning workflow.
            - "one_shot": Single-step execution mode. Sets up a simplified context with a
              single-step plan and minimal planning overhead.
            - "human_in_the_loop": Human-in-the-loop mode, similar to "one_shot" with simplified context.
            Defaults to "default".
            
        step : int or None, optional
            Step number for context carryover workflow. This is recorded in self.step for
            workflows that require tracking progress across multiple solve calls. If None,
            no step tracking is performed.
            
        max_rounds : int, optional
            Maximum number of conversation rounds allowed in the group chat. The chat will
            terminate after this many rounds even if the task is not fully completed.
            Defaults to 10.
            
        Returns
        -------
        None
            This method does not return a value. Instead, it stores results in the following
            instance variables:
            - self.final_context : dict
                A deep copy of the final context variables after the group chat completes.
                Contains all shared context including task status, plans, and agent states.
            - self.last_agent : Agent
                The last agent that participated in the group chat.
            - self.chat_result : ChatResult
                The complete chat result object containing the full conversation history
                and metadata.
            - self.step : int or None
                The step number that was passed to this method (for context carryover).
                
        Side Effects
        ------------
        - Creates and populates work directories (database, codebase, chats, time, cost)
        - Adds codebase path to sys.path for module imports
        - Resets all agents in the system
        - Clears work directory if clear_work_dir_bool is True
        - Modifies self.shared_context (via deep copy, original is preserved)
        
        Notes
        -----
        - In "one_shot" or "human_in_the_loop" mode, a simplified shared context is created with a
          single-step plan and minimal planning structure.
        - The method uses AutoPattern to coordinate agent interactions and handoffs.
        - All agents are reset before starting the group chat to ensure clean state.
        - The codebase directory is added to sys.path, allowing agents to import modules
          from the generated codebase.
          
        Examples
        --------
        >>> agent = CMBAgent()
        >>> agent.solve("Write a Python function to calculate fibonacci numbers")
        >>> # Access results via instance variables
        >>> print(agent.final_context['main_task'])
        >>> print(agent.chat_result.chat_history)
        
        >>> # Use one-shot mode for simple tasks
        >>> agent.solve("What is 2+2?", mode="one_shot", initial_agent="engineer")
        
        >>> # Use with context carryover
        >>> agent.solve("Step 1: Research topic", step=1, max_rounds=5)
        >>> agent.solve("Step 2: Write code", step=2, max_rounds=5)
        """
        self.step = step ## record the step for the context carryover workflow 
        this_shared_context = copy.deepcopy(self.shared_context)
        
        if mode == "one_shot" or mode == "human_in_the_loop":
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
                                        'idea_maker_append_instructions': '',
                                        'idea_hater_append_instructions': '',
                                        }

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
        """
        Retrieve an agent wrapper object by its name.
        
        Searches through all registered agents in the system and returns the agent
        wrapper object that matches the specified name. The returned object contains
        both the agent instance and metadata about the agent.
        
        Parameters
        ----------
        name : str
            The name of the agent to retrieve. Must match the 'name' value in the
            agent's info dictionary. Common agent names include: 'engineer', 'researcher',
            'planner', 'engineer_response_formatter',
            'researcher_response_formatter', etc.
            
        Returns
        -------
        AgentWrapper
            The agent wrapper object containing:
            - agent : Agent
                The actual agent instance (accessible via .agent attribute)
            - info : dict
                Dictionary containing agent metadata including:
                - 'name': The agent's name
                - 'instructions': The agent's system instructions
                - Other agent-specific configuration
            - name : str
                The agent's name (also accessible as attribute)
                
        Raises
        ------
        SystemExit
            If no agent with the specified name is found. The method prints an
            error message and calls sys.exit(), terminating the program.
            
        Notes
        -----
        - This method searches through self.agents, which is populated during
          initialization via init_agents().
        - The returned object is the wrapper, not the raw agent. To get the raw
          agent instance, use get_agent_from_name() instead, or access the .agent
          attribute of the returned object.
        - Agent names are case-sensitive and must match exactly.
        - This method will terminate the program if the agent is not found, so
          ensure the agent name is valid before calling.
          
        See Also
        --------
        get_agent_from_name : Returns the raw agent instance instead of the wrapper.
        
        Examples
        --------
        >>> agent = CMBAgent()
        >>> engineer_wrapper = agent.get_agent_object_from_name('engineer')
        >>> print(engineer_wrapper.info['instructions'])
        >>> engineer_instance = engineer_wrapper.agent
        >>> 
        >>> # Access agent metadata
        >>> planner = agent.get_agent_object_from_name('planner')
        >>> query = planner.info['instructions'].format(main_task="Solve this")
        """
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

        # This automatically loads all the agents from the agents folder
        imported_agents = import_agents()

        ## this will store classes for each agents
        self.agent_classes = {}

        for k in imported_agents.keys():
            self.agent_classes[imported_agents[k]['agent_name']] = imported_agents[k]['agent_class']

        if cmbagent_debug:
            print('self.agent_classes: ', self.agent_classes)

        if cmbagent_debug:
            print('self.agent_classes after update: ')
            print()
            for agent_class, value in self.agent_classes.items():
                print(f'{agent_class}: {value}')
                print()

        # all agents
        self.agents = []

        if cmbagent_debug:
            print('self.agent_classes after list update: ')
            print()
            for agent_class, value in self.agent_classes.items():
                print(f'{agent_class}: {value}')
                print()

        # remove agents that are not set to be skipped
        if self.skip_memory:
            # self.agent_classes.pop('memory', None)
            pass

        if self.skip_executor:
            self.agent_classes.pop('executor', None)

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

        self.agent_names =  [agent.name for agent in self.agents]
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





