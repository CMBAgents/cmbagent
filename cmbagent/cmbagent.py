import os
import logging
import importlib
import requests
import autogen 
import ast
import json
import sys
import pandas as pd
import datetime
from typing import Any, Dict
from IPython.display import display
from collections import defaultdict
from .utils import work_dir as work_dir_default
from .utils import path_to_assistants,config_list_from_json,path_to_apis,OpenAI,Image,default_chunking_strategy,default_top_p,default_temperature,default_select_speaker_prompt_template,default_select_speaker_message_template
from .utils import default_max_round, default_groupchat_intro_message
from pprint import pprint
from .base_agent import CmbAgentGroupChat, CmbAgentSwarmAgent
from cmbagent.engineer.engineer import EngineerAgent
from cmbagent.planner.planner import PlannerAgent
from cmbagent.executor.executor import ExecutorAgent
from cmbagent.admin.admin import AdminAgent
from cmbagent.summarizer.summarizer import SummarizerAgent
from cmbagent.rag_software_formatter.rag_software_formatter import RagSoftwareFormatterAgent
from pydantic import BaseModel
# import yaml
from ruamel.yaml import YAML
from typing import List
from pprint import pprint
from autogen import  AfterWorkOption, AFTER_WORK, ON_CONDITION, SwarmResult

from cmbagent.cmbagent_swarm_agent import initiate_cmbagent_swarm_chat
from cmbagent.structured_output import EngineerResponse, PlannerResponse, SummarizerResponse, RagSoftwareFormatterResponse

from sys import exit


def import_rag_agents():        
    imported_rag_agents = {}
    for filename in os.listdir(path_to_assistants):
        if filename.endswith(".py") and filename != "__init__.py" and filename[0] != ".":
            module_name = filename[:-3]  # Remove the .py extension
            class_name = ''.join([part.capitalize() for part in module_name.split('_')]) + 'Agent'
            module_path = f"cmbagent.assistants.{module_name}"
            module = importlib.import_module(module_path)
            agent_class = getattr(module, class_name)
            imported_rag_agents[class_name] = {}
            imported_rag_agents[class_name]['agent_class'] = agent_class
            imported_rag_agents[class_name]['agent_name'] = module_name
    return imported_rag_agents

def make_rag_agents(make_new_rag_agents):
    """
    Create new RAG agents based on the provided list of agent names.

    This function generates Python and YAML files for each new agent specified
    in the 'make_new_rag_agents' list. It creates:
    1. A Python file with a basic agent class structure.
    2. A YAML file with initial configuration for the agent.
    3. A data folder for each agent to store relevant files.

    Args:
        make_new_rag_agents (list): A list of strings, where each string is the
                                    name of a new agent to be created.

    Returns:
        dict: A dictionary where keys are agent names and values are paths to
              their respective data folders.

    Note:
    - The Python file will contain a class definition inheriting from BaseAgent.
    - The YAML file will include basic configuration like name, instructions,
      and tool definitions.
    - Existing files with the same names will be overwritten.
    - A new data folder is created for each agent in the assistants directory.
    """
    data_folders = {}
    for agent_name in make_new_rag_agents:
        # Create the Python file for the agent
        agent_file_path = os.path.join(path_to_assistants, f"{agent_name}.py")
        with open(agent_file_path, "w") as f:
            f.write(f"""import os
from cmbagent.base_agent import BaseAgent


class {agent_name.capitalize()}Agent(BaseAgent):

    def __init__(self, llm_config=None, **kwargs):

        agent_id = os.path.splitext(os.path.abspath(__file__))[0]

        super().__init__(llm_config=llm_config, agent_id=agent_id, **kwargs)
""")

        # Create the YAML file for the agent
        yaml = YAML()
        yaml.preserve_quotes = True
        yaml.indent(mapping=2, sequence=4, offset=2)
        
        yaml_file_path = os.path.join(path_to_assistants, f"{agent_name}.yaml")

        yaml_content = {
            "name": f"{agent_name}_agent",
            "instructions": f"You are the {agent_name}_agent in the team. Your role is to assist with tasks related to {agent_name}.",
            "assistant_config": {
                "assistant_id": "asst_ijk",
                "tools": [
                    {
                        "type": "file_search"
                    }
                ],
                "tool_resources": {
                    "file_search": {
                        "vector_store_ids": [
                            "vs_xyz"
                        ]
                    }
                }
            },
            "description": f"This is the {agent_name}_agent: a retrieval agent that provides assistance with {agent_name.upper()}. It must perform retrieval augmented generation and include the <filenames> in the response.",
            "allowed_transitions": [
                "admin"
            ]
        }
        
        with open(yaml_file_path, "w") as f:
            yaml.dump(yaml_content, f)

        print(f"Created {agent_name} agent files: {agent_file_path} and {yaml_file_path}")
        # Create a folder for the agent's data
        agent_data_folder = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'data', agent_name)
        os.makedirs(agent_data_folder, exist_ok=True)
        print(f"Created data folder for {agent_name} agent: {agent_data_folder}")
        print(f"Please deposit any relevant files for the {agent_name} agent in this folder.")

    # Return a dictionary with the full paths to the agent data folders
    data_folders = {}
    data_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'data')
    for agent_folder in os.listdir(data_dir):
        full_path = os.path.join(data_dir, agent_folder)
        if os.path.isdir(full_path):
            data_folders[agent_folder] = full_path
    return data_folders





class CMBAgent:

    logging.disable(logging.CRITICAL)



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
                 agent_list = None,
                 verbose = False,
                 reset_assistant = False,
                 agent_instructions = None,
                 agent_descriptions = None,
                 agent_temperature = None,
                 agent_top_p = None,
                #  vector_store_ids = None,
                 chunking_strategy = None,
                 select_speaker_prompt = None,
                 select_speaker_message = None,
                 intro_message = None,
                 set_allowed_transitions = None,
                 skip_executor = False,
                 skip_memory = True,
                 work_dir = None,
                 agent_llm_configs = None,
                 agent_type = None,# 'swarm',
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
            #  example:
            #  chunking_strategy = {
            # 'planck_agent': 
            #     {
            #     "type": "static",
            #     "static": {
            #       "max_chunk_size_tokens": 3300, # reduce size to ensure better context integrity
            #       "chunk_overlap_tokens": 1000 # increase overlap to maintain context across chunks
            #     }
            # }
            # }
            # example for agent_temperature and agent_top_p:
            # agent_temperature = {
            # 'planck_agent': 0.000001
            # }
            # agent_top_p = {
            # 'planck_agent': 0.1,
            # }
            # agent instruction example:
            # agent_instructions = {
            # 'classy_sz_agent': "You are a clown. "
            # }
            reset_assistant (List of strings, optional): List of agents to reset the assistant. Defaults to False.
            # example:
            # reset_assistant = [
            # 'classy_sz',
            # ]
            
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



        self.kwargs = kwargs

        self.skip_executor = skip_executor

        # self.make_new_rag_agents = make_new_rag_agents
        self.set_allowed_transitions = set_allowed_transitions

        self.vector_store_ids = None

        self.logger = logging.getLogger(__name__)

        self.non_rag_agents = ['engineer', 'planner', 'executor', 'admin', 'summarizer', 'rag_software_formatter']

        self.agent_list = agent_list

        self.skip_memory = skip_memory

        if not self.skip_memory and 'memory' not in agent_list:
            self.agent_list.append('memory')


        self.verbose = verbose

        self.work_dir = work_dir if work_dir else work_dir_default

        self.path_to_assistants = path_to_assistants

        self.logger.info(f"Autogen version: {autogen.__version__}")

        llm_config = config_list_from_json(f"{path_to_apis}/{platform}_{model}.json")

        if llm_api_key is not None:
            llm_config[0]['api_key'] = llm_api_key
        else:
            llm_config[0]['api_key'] = os.getenv("OPENAI_API_KEY")

        if llm_api_type is not None:
            llm_config[0]['api_type'] = llm_api_type
        else:
            llm_config[0]['api_type'] = 'openai'

        self.llm_api_key = llm_config[0]['api_key']

        self.logger.info(f"Path to APIs: {path_to_apis}")

        self.cache_seed = cache_seed

        self.llm_config = {
                        "cache_seed": self.cache_seed,  # change the cache_seed for different trials
                        "temperature": temperature,
                        "top_p": top_p,
                        "config_list": llm_config,
                        "timeout": timeout,
                    }

        self.logger.info("LLM Configuration:")

        for key, value in self.llm_config.items():

            self.logger.info(f"{key}: {value}")

        self.agent_type = agent_type

        self.init_agents(agent_llm_configs=agent_llm_configs) # initialize agents

        
        self.check_assistants(reset_assistant=reset_assistant) # check if assistants exist

        self.push_vector_stores(make_vector_stores, chunking_strategy, verbose = verbose) # push vector stores

        self.set_planner_instructions() # set planner instructions


        if self.verbose:
            print("Setting up agents:")


        # then we set the agents, note that self.agents is set in init_agents
        for agent in self.agents:

            agent.agent_type = self.agent_type

            if self.skip_executor:
                if agent.name == 'executor':
                    continue

            print(f"\t- {agent.name}")

            instructions = agent_instructions[agent.name] if agent_instructions and agent.name in agent_instructions else None
            description = agent_descriptions[agent.name] if agent_descriptions and agent.name in agent_descriptions else None
            agent_kwargs = {}

            if instructions is not None:

                agent_kwargs['instructions'] = instructions

            if description is not None:

                agent_kwargs['description'] = description

            if agent.name not in self.non_rag_agents: ## loop over all rag agents 


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

            
                if agent.set_agent(**agent_kwargs) == 1:

                    print(f"setting make_vector_stores=['{agent.name.removesuffix('_agent')}'],")
                    
                    self.push_vector_stores([agent.name.removesuffix('_agent')], chunking_strategy, verbose = verbose)

                    agent_kwargs['vector_store_ids'] = self.vector_store_ids[agent.name] 

                    agent.set_agent(**agent_kwargs) 

                else:
                    # see above for trick on how to make vector store if it is not found. 
                    agent.set_agent(**agent_kwargs)

            else: ## set all non-rag agents
                
                agent.set_agent(**agent_kwargs)

            ## debug print to help debug
            #print('in cmbagent.py self.agents instructions: ',instructions)
            #print('in cmbagent.py self.agents description: ',description)


        self.allowed_transitions = self.get_allowed_transitions()
        


        if self.verbose:
            self.show_allowed_transitions()

        if self.verbose:
            print("Planner instructions:")
            print(self.planner.info['instructions'])



        select_speaker_prompt_template = select_speaker_prompt if select_speaker_prompt else default_select_speaker_prompt_template
        select_speaker_message_template = select_speaker_message if select_speaker_message else default_select_speaker_message_template
        groupchat_intro_message = intro_message if intro_message else default_groupchat_intro_message

        self.rag_agents = [agent.agent for agent in self.agents if agent.name not in self.non_rag_agents]
        
        
        if self.agent_type == 'swarm':
            
            intro_message = default_groupchat_intro_message

            for agent in self.agents:
                intro_message += f'{agent.name}, '
    
            self.manager = CmbAgentSwarmAgent(name= "cmbagent", 
                                              llm_config=self.llm_config,
                                              system_message= intro_message,
                                              description="You are the groupchat manager. You are the first to speak.")

        else:
            # cmbagent debug print: 
            # print("--> in cmbagent.py self.rag_agents: ", self.rag_agents)
            self.groupchat = CmbAgentGroupChat(
                                    agents=[agent.agent for agent in self.agents],
                                    rag_agents=self.rag_agents,
                                    allowed_or_disallowed_speaker_transitions=self.allowed_transitions,
                                    speaker_transitions_type="allowed",
                                    messages=[],
                                    speaker_selection_method = "auto",
                                    max_round=max_round,
                                    select_speaker_auto_verbose=False,
                                    send_introductions=True,
                                    admin_name="admin",
                                    select_speaker_prompt_template=select_speaker_prompt_template,
                                    select_speaker_message_template=select_speaker_message_template,
                                    verbose=self.verbose,
                                    agent_type=self.agent_type
                                    )


            self.manager = autogen.GroupChatManager(groupchat=self.groupchat,
                                                    name="cmbagent",
                                                    llm_config=self.llm_config)

        
            self.groupchat.DEFAULT_INTRO_MSG  = groupchat_intro_message




            for agent in self.groupchat.agents: 

                agent.reset()
    

    def display_cost(self):
        '''display full cost dictionary'''
        cost_dict = defaultdict(list)
        all_agents = [agent.agent for agent in self.agents] + self.groupchat.new_conversable_agents
        for agent in all_agents:
            if hasattr(agent, 'cost_dict') and agent.cost_dict['Agent']:
                name = agent.cost_dict['Agent'][0].replace('admin (', '').replace(')', '').replace('_', ' ')
                if name in cost_dict['Agent']:
                    idx = cost_dict['Agent'].index(name)
                    for field in ['Cost', 'Prompt Tokens', 'Completion Tokens', 'Total Tokens']:
                        cost_dict[field][idx] += sum(agent.cost_dict[field])
                else:
                    cost_dict['Agent'].append(name)
                    for field in ['Cost', 'Prompt Tokens', 'Completion Tokens', 'Total Tokens']:
                        cost_dict[field].append(sum(agent.cost_dict[field]))
        df = pd.DataFrame(cost_dict)
        columns_to_sum = df.select_dtypes(include='number').columns
        totals = df[columns_to_sum].sum()
        df.loc['Total'] = pd.concat([pd.Series({'Name': 'Total'}), totals])
        display(df)
        return
    

    def update_memory_agent(self):
        
        response = input('''Do you want to save this task summary to the "memory agent" vector stores? This will aid you and others in solving similar tasks in the future. Please only save the task if it has been completed successfully. Type "yes" or "no". ''').strip().lower()
        

        if 'yes' in response:
            print('Asking summarizer to generate summary')
            print('The summary will be json formatted.')
            print('\n\n')
            summary_message = """
            We will now summarize the session.
            """

            previous_state = f"{self.groupchat.messages}"

            # Convert string to Python dictionary
            dict_representation = ast.literal_eval(previous_state)

            # Convert dictionary to JSON string
            json_string = json.dumps(dict_representation)

            # print("previous state: ", json_string)
            # exit()
            last_agent, last_message = self.manager.resume(messages=json_string)

            self.manager.cmbagent_summarizer = True

            self.session = self.summarizer.agent.initiate_chat(recipient=self.manager,
                                                          message=summary_message,
                                                          clear_history=False)


            # Extract the content
            content = self.groupchat.messages[-1]['content']

            # Parse the content string to a Python dictionary
            content_dict = json.loads(content)

            # Save to a JSON file
            id = f'{datetime.datetime.now():%Y-%m-%d_%H:%M:%S}'
            with open(os.getenv('CMBAGENT_DATA')+ '/data/memory/' + f'summary_{id}.json', 'w') as json_file:
                json.dump(content_dict, json_file, indent=4)
            # Pretty-print the JSON
            pretty_json = json.dumps(content_dict, indent=4)

            print("Formatted JSON output:\n")
            print(pretty_json)
            # print("\nNested structure with pprint:\n")
            # pprint(content_dict)
            # id = f'{datetime.datetime.now():%Y-%m-%d_%H:%M:%S}'
            # with open(os.getenv('CMBAGENT_DATA')+ '/data/memory/' + f'summary_{id}.json', 'w') as json_file:
            #     json.dump(pretty_json, json_file, indent=4) 

            # Push to memory agent vector store
            self.push_vector_stores(['memory'], None, verbose = True)

            print("The memory vector store has been updated. The session will now be closed.")
            # print('Updated memory agent\'s vector stores.')


        if 'yes' not in response:
            print('Task summary not added to memory agent\'s vector stores.')
            return
        
        # previous_state = f"{self.groupchat.messages}"

        # Convert string to Python dictionary
        # dict_representation = ast.literal_eval(previous_state)

        # Convert dictionary to JSON string and save file
        # json_string = json.dumps(dict_representation)
        # id = f'{datetime.datetime.now():%Y-%m-%d_%H:%M:%S}'
        # with open(os.getenv('CMBAGENT_DATA')+ '/data/memory/' + f'summary_{id}.json', 'w') as json_file:
        #     json.dump(json_string, json_file, indent=4)

        # Push to memory agent vector store
        # self.push_vector_stores(['memory'], None, verbose = False)
        # print('Updated memory agent\'s vector stores.')

        return
        


    def solve(self, task):

        if self.agent_type == 'swarm':

            swarm_agents = [agent.agent for agent in self.agents if agent.name != 'admin']
            swarm_agent_names = [agent.name for agent in self.agents if agent.name != 'admin']
            if not self.skip_memory:
                memory_agent = swarm_agents[swarm_agent_names.index('memory_agent')]
            rag_software_formatter = swarm_agents[swarm_agent_names.index('rag_software_formatter')]


            # context variables
            context_variables = {
                "plan": "",                                 # the proposed plan
            }

            def save_plan(final_plan: str, context_variables: Dict[str, Any]) -> SwarmResult:
                """Store and plan"""
                context_variables["plan"] = final_plan
                # This will update the context variables and then transfer to the Structured Output agent
                return SwarmResult(
                    agent="rag_software_formatter", context_variables=context_variables, values="Plan recorded and confirmed."
                )
            self.planner.agent.add_single_function(save_plan)
            

            # hand offs among agents
            if not self.skip_memory:
                memory_agent.register_hand_off(hand_to=[AFTER_WORK(self.planner.agent)])
            else:
                self.planner.agent.register_hand_off(
                    hand_to=[AFTER_WORK(AfterWorkOption.REVERT_TO_USER),  # Revert to the user for more information
                    ])


            groupchat_intro_message = default_groupchat_intro_message  


            chat_history, context_variables, last_active_agent, groupchat = \
                        initiate_cmbagent_swarm_chat(
                                    initial_agent = self.planner.agent, 
                                    messages = groupchat_intro_message + task,
                                    agents = swarm_agents,
                                    rag_agents = self.rag_agents,
                                    send_introductions = True,
                                    admin_name = 'cmbagent',
                                    user_agent = self.admin.agent,
                                    max_rounds = 100,
                                    context_variables = context_variables,
                                    after_work = AfterWorkOption.REVERT_TO_USER,
                                    cost = 0,
                                    verbose = self.verbose
                                    )

            self.chat_history = chat_history
            self.context_variables = context_variables
            self.last_active_agent = last_active_agent
            self.groupchat = groupchat
            self.groupchat.DEFAULT_INTRO_MSG  = groupchat_intro_message
            print('context variables: ', context_variables)
            print('last_active_agent: ', last_active_agent)

            # display full cost dictionary
            self.display_cost()

            # ask user if they want to update memory agent
            if not self.skip_memory:
                self.update_memory_agent()


        else:

            self.session = self.admin.agent.initiate_chat(self.manager,message = task)

            # display full cost dictionary
            self.display_cost()

            # ask user if they want to update memory agent
            # self.update_memory_agent()
            if not self.skip_memory:
                self.update_memory_agent()

        

    def restore(self):
        """
        Restore the previous state of the group chat. 

        This method restores the previous state of the group chat by:
        1. Converting the stored messages back to a Python dictionary.
        2. Converting the dictionary to a JSON string.
        3. Resuming the group chat manager with the restored messages.
        4. Initiating a new chat session with the last active agent and message.

        Returns:
            None
        """

        

        previous_state = f"{self.groupchat.messages}"

        # Convert string to Python dictionary
        dict_representation = ast.literal_eval(previous_state)

        # Convert dictionary to JSON string
        json_string = json.dumps(dict_representation)

        # Prepare the group chat for resuming
        last_agent, last_message = self.manager.resume(messages=json_string)

        if self.agent_type == 'swarm':
            # Resume the chat using the last agent and message
            self.session = last_agent.initiate_cmbagent_swarm_chat(recipient=self.manager,
                                                    message=last_message,
                                                    clear_history=False)

        else:
            # Resume the chat using the last agent and message
            self.session = last_agent.initiate_chat(recipient=self.manager,
                                                message=last_message,
                                                clear_history=False)


    def get_agent_from_name(self,name):
        for agent in self.agents:
            if agent.info['name'] == name:
                return agent.agent
        print(f"get_agent_from_name: agent {name} not found")
        sys.exit()


    def get_allowed_transitions(self):

        allowed_transitions = {}

        for agent in self.agents:

            transition_list = []

            for name in agent.info['allowed_transitions']:

                if name not in self.agent_names:
                    continue

                transition_list.append(self.get_agent_from_name(name))

            allowed_transitions[agent.agent] = transition_list

        if self.set_allowed_transitions is not None:
            
            for name in self.set_allowed_transitions.keys():

                if name not in self.agent_names:
                    print(f"get_allowed_transitions: agent {name} not found")
                    break

                transition_list = []

                for name_out in self.set_allowed_transitions[name]:

                    if name_out not in self.agent_names:
                        print(f"get_allowed_transitions: agent {name_out} not found")
                        break



                    transition_list.append(self.get_agent_from_name(name_out))

                if transition_list:
                    
                    allowed_transitions[self.get_agent_from_name(name)] = transition_list

        return allowed_transitions


    def show_allowed_transitions(self):

        print("Allowed transitions:")

        for agent, transitions in self.allowed_transitions.items():

            print(f"{agent.name} -> {', '.join([t.name for t in transitions])}")

        print()



    def push_vector_stores(self, make_vector_stores, chunking_strategy, verbose = False):

        if make_vector_stores == False:
            return

        client = OpenAI(api_key = self.llm_api_key)

        # 1. identify rag agents and set store names

        store_names = []
        rag_agents = []

        for agent in self.agents:


            if type(make_vector_stores) == list and agent.info['name'] not in make_vector_stores and agent.info['name'].replace('_agent', '') not in make_vector_stores:
                continue

            if 'assistant_config' in agent.info:

                if 'file_search' in agent.info['assistant_config']['tool_resources'].keys():

                    print(f"Updating vector store for {agent.info['name']}")

                    # print(agent.info['assistant_config']['assistant_id'])

                    # print(agent.info['assistant_config']['tool_resources']['file_search'])

                    store_names.append(f"{agent.info['name']}_store")

                    rag_agents.append(agent)


        # 2. collect all vector stores

        # Set the headers for authentication
        headers = {
            "Authorization": f"Bearer {self.llm_api_key}",
            "OpenAI-Beta": "assistants=v2"
        }

        # Define the URL endpoint for listing vector stores
        url = "https://api.openai.com/v1/vector_stores"

        # Send a GET request to list vector stores
        response = requests.get(url, headers=headers)

        # Check if the request was successful
        if response.status_code == 200:

            vector_stores = response.json()

        else:

            print("Failed to retrieve vector stores:", response.status_code, response.text)


        # 3. delete old vector stores if they exist and write new ones

        # Find all vector stores by name and collect their IDs
        vector_store_ids = {}
        for vector_store_name,rag_agent in zip(store_names,rag_agents):

            # print('dealing with: ',vector_store_name)

            matching_vector_store_ids = [
                store['id'] for store in vector_stores['data'] if store['name'] == vector_store_name
            ]

            if matching_vector_store_ids:

                # print(f"Vector store IDs for '{vector_store_name}':", matching_vector_store_ids)

                for vector_store_id in matching_vector_store_ids:

                    # Define the URL endpoint for deleting a vector store by ID
                    delete_url = f"https://api.openai.com/v1/vector_stores/{vector_store_id}"

                    # Send a DELETE request to delete the vector store
                    delete_response = requests.delete(delete_url, headers=headers)

                    # Check if the request was successful
                    if delete_response.status_code == 200:

                        # print(f"Vector store with ID '{vector_store_id}' deleted successfully.")

                        continue

                    else:

                        print("Failed to delete vector store:", delete_response.status_code, delete_response.text)

            else:

                print(f"No vector stores found with the name '{vector_store_name}'.")

            # print()

            # print(rag_agent.name)
            chunking_strategy = chunking_strategy[rag_agent.name] if chunking_strategy and rag_agent.name in chunking_strategy else default_chunking_strategy
            if verbose:
                print(f"{rag_agent.name}: chunking strategy: ")
                pprint(chunking_strategy)
            # print()

            # print('calling client.beta.vector_stores.create')
            # Create a vector store called "planck_store"
            vector_store = client.beta.vector_stores.create(name=vector_store_name,
                                                            chunking_strategy=chunking_strategy)

            # print('created vector store with id: ',vector_store.id)
            # print('\n')

            # Initialize a list to hold the file paths
            file_paths = []

            assistant_data = os.getenv('CMBAGENT_DATA') + "/data/" + vector_store_name.removesuffix('_agent_store')


            print("Files to upload:")
            for root, dirs, files in os.walk(assistant_data):
                # Filter out unwanted directories like .ipynb_checkpoints
                dirs[:] = [d for d in dirs if not d.startswith('.')]

                for file in files:

                    if file.startswith('.') or file.endswith('.ipynb')  or file.endswith('.yaml') or file.endswith('.txt'):

                        continue

                    print(f"\t - {file}")

                    # Get the absolute path of each file
                    file_paths.append(os.path.join(root, file))

            # Ready the files for upload to OpenAI

            file_streams = [open(path, "rb") for path in file_paths]

            # Use the upload and poll SDK helper to upload the files, add them to the vector store,
            # and poll the status of the file batch for completion.
            file_batch = client.beta.vector_stores.file_batches.upload_and_poll(
                    vector_store_id=vector_store.id,
                    files=file_streams
                    )

            # You can print the status and the file counts of the batch to see the result of this operation.
            print(file_batch.status)
            print(file_batch.file_counts)

            rag_agent.info['assistant_config']['tool_resources']['file_search']['vector_store_ids'] = [vector_store.id]

            print(f'{rag_agent.name}: uploaded assistant data to vector store with id: ',vector_store.id)
            print('\n')
            new_vector_store_ids = {rag_agent.name : vector_store.id}
            vector_store_ids.update(new_vector_store_ids)

        self.vector_store_ids = vector_store_ids

        print("vector stores updated")

        for key, value in self.vector_store_ids.items():
            print(f"'{key}': '{value}',")

        # vector_store_ids = self.vector_store_ids

        for agent_name, vector_id in self.vector_store_ids.items():
            update_yaml_preserving_format(f"{path_to_assistants}/{agent_name.replace('_agent', '') }.yaml", agent_name, vector_id)


    def init_agents(self,agent_llm_configs=None):

        imported_rag_agents = import_rag_agents()
        # print('imported_rag_agents: ', imported_rag_agents)
        # print("making new rag agents: ", self.make_new_rag_agents)
        # make_rag_agents(self.make_new_rag_agents)
        # imported_rag_agents = import_rag_agents()
        # print('imported_rag_agents: ', imported_rag_agents)

        ## this will store classes for each agents
        self.agent_classes = {}

        for k in imported_rag_agents.keys():

            self.agent_classes[imported_rag_agents[k]['agent_name']] = imported_rag_agents[k]['agent_class']

        # print('self.agent_classes: ', self.agent_classes)
        # exit()

        self.agent_classes.update({

            'engineer': EngineerAgent,
            'planner': PlannerAgent,
            'executor': ExecutorAgent,
            'summarizer': SummarizerAgent,
            'admin': AdminAgent,
            'rag_software_formatter': RagSoftwareFormatterAgent
        })


        ### by default are always here
        
        engineer_llm_config = self.llm_config.copy()
        engineer_llm_config['config_list'] = [
                        {
                        "model": self.llm_config['config_list'][0]['model'],
                        "api_key": self.llm_config['config_list'][0]['api_key'],
                        "api_type": self.llm_config['config_list'][0]['api_type'],
                        'response_format': EngineerResponse,
                        }
        ]

        planner_llm_config = self.llm_config.copy()
        planner_llm_config['config_list'] = [
                        {
                        "model": self.llm_config['config_list'][0]['model'],
                        "api_key": self.llm_config['config_list'][0]['api_key'],
                        "api_type": self.llm_config['config_list'][0]['api_type'],
                        'response_format': PlannerResponse,
                        }
        ]

        summarizer_llm_config = self.llm_config.copy()
        summarizer_llm_config['config_list'] = [
                        {
                        "model": self.llm_config['config_list'][0]['model'],
                        "api_key": self.llm_config['config_list'][0]['api_key'],
                        "api_type": self.llm_config['config_list'][0]['api_type'],
                        'response_format': SummarizerResponse,
                        }
        ]

        rag_software_formatter_llm_config = self.llm_config.copy()
        rag_software_formatter_llm_config['config_list'] = [
                        {
                        "model": self.llm_config['config_list'][0]['model'],
                        "api_key": self.llm_config['config_list'][0]['api_key'],
                        "api_type": self.llm_config['config_list'][0]['api_type'],
                        'response_format': RagSoftwareFormatterResponse,
                        }
        ]

        classy_sz_llm_config = self.llm_config.copy()
        classy_sz_llm_config['config_list'] = [
                        {
                        "model": self.llm_config['config_list'][0]['model'],
                        "api_key": self.llm_config['config_list'][0]['api_key'],
                        "api_type": self.llm_config['config_list'][0]['api_type'],
                        'response_format': RagSoftwareFormatterResponse, # doesnt work yet. Currently using rag_software_formatter after this agent. 
                        }
        ]

        ## set custom llm configs if provided
        if agent_llm_configs is not None:

            engineer_llm_config['config_list'] = [agent_llm_configs['engineer']] if 'engineer' in agent_llm_configs else self.llm_config['config_list']
            
            planner_llm_config['config_list'] = [agent_llm_configs['planner']] if 'planner' in agent_llm_configs else self.llm_config['config_list']

            summarizer_llm_config['config_list'] = [agent_llm_configs['summarizer']] if 'summarizer' in agent_llm_configs else self.llm_config['config_list']
        

        self.engineer = EngineerAgent(llm_config=engineer_llm_config)
        
        self.planner = PlannerAgent(llm_config=planner_llm_config)

        self.executor = ExecutorAgent(llm_config=self.llm_config, 
                                       work_dir=self.work_dir)

        self.summarizer = SummarizerAgent(llm_config=summarizer_llm_config)

        self.rag_software_formatter = RagSoftwareFormatterAgent(llm_config=rag_software_formatter_llm_config)

        # the administrator (to interact with us humans)
        self.admin = AdminAgent()


        # all agents

        self.agents = [self.admin,
                       self.planner,
                       self.engineer,
                       self.rag_software_formatter]

        if not self.skip_memory:
            self.agents.append(self.summarizer)
        
        if not self.skip_executor:
            self.agents.append(self.executor)

        if self.agent_list is None:

            self.agent_list = list(self.agent_classes.keys())

        # Drop entries from self.agent_classes that are not in self.agent_list
        self.agent_classes = {k: v for k, v in self.agent_classes.items() if k in self.agent_list or k in ['summarizer', 
                                                                                                            'engineer', 
                                                                                                            'planner', 
                                                                                                            'executor', 
                                                                                                            'rag_software_formatter',
                                                                                                            'admin']}


        for agent_name in self.agent_list:

            if agent_name in self.agent_classes and agent_name not in ['engineer',
                                                                       'planner',
                                                                       'executor',
                                                                       'summarizer',
                                                                       'rag_software_formatter',
                                                                       'admin']:
                agent_class = self.agent_classes[agent_name]

                # print('agent_name: ', agent_name)

                if agent_name == 'classy_sz':
                    llm_config = classy_sz_llm_config
                    # print('in cmbagent.py: classy_sz_llm_config: ', classy_sz_llm_config)
                else:
                    llm_config = self.llm_config

                agent_instance = agent_class(llm_config=llm_config,agent_type=self.agent_type)

                print('agent_type: ', agent_instance.agent_type)

                setattr(self, agent_name, agent_instance)

                self.agents.append(agent_instance)


        agent_keys = self.agent_classes.keys()

        self.agent_names =  [f"{key}_agent" if key not in ['engineer', 'planner', 'executor', 'admin', 'summarizer', 'rag_software_formatter'] else key for key in agent_keys]

        if self.skip_executor:
            self.agent_names.remove('executor')

        if self.skip_memory:
            self.agent_names.remove('summarizer')

        if self.verbose:

            print("Using following agents: ", self.agent_names)
            print()

    def create_assistant(self, client, agent):

        print(f"-->Creating assistant {agent.name}")

        print(f"--> llm_config: {self.llm_config}")

        print(f"--> agent.llm_config: {agent.llm_config}")

        new_assistant = client.beta.assistants.create(
            name=agent.name,
            instructions=agent.info['instructions'],
            tools=[{"type": "file_search"}],
            tool_resources={"file_search": {"vector_store_ids":[]}},
            model=agent.llm_config['config_list'][0]['model'],
            # response_format=agent.llm_config['config_list'][0]['response_format']
        )
        print("New assistant created.")
        print(f"--> New assistant id: {new_assistant.id}")
        print(f"--> New assistant response format: {new_assistant.response_format}")
        print("\n")

        return new_assistant


    def check_assistants(self, reset_assistant=[]):

        client = OpenAI(api_key = self.llm_api_key)
        available_assistants = client.beta.assistants.list(
            order="desc",
            limit="100",
        )


        # Create a list of assistant names for easy comparison
        assistant_names = [d.name for d in available_assistants.data]
        assistant_ids = [d.id for d in available_assistants.data]

        for agent in self.agents:

            if agent.name not in self.non_rag_agents:
                
                print(f"Checking agent: {agent.name}")

                # Check if agent name exists in the available assistants
                if agent.name in assistant_names:

                    print(f"Agent {agent.name} exists in available assistants with id: {assistant_ids[assistant_names.index(agent.name)]}")

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
                            print(f"--> Assistant ID between yaml and openai do not match.")
                            print(f"--> Assistant ID from your yaml: {assistant_id}")
                            print(f"--> Assistant ID in openai: {assistant_ids[assistant_names.index(agent.name)]}")
                            print("--> We will use the assistant id from openai")
                            agent.info['assistant_config']['assistant_id'] = assistant_ids[assistant_names.index(agent.name)]
                            print(f"--> Updating yaml file with new assistant id: {assistant_ids[assistant_names.index(agent.name)]}")
                            update_yaml_preserving_format(f"{path_to_assistants}/{agent.name.replace('_agent', '') }.yaml", agent.name, assistant_ids[assistant_names.index(agent.name)], field = 'assistant_id')
                    
                else:

                    new_assistant = self.create_assistant(client, agent)
                    agent.info['assistant_config']['assistant_id'] = new_assistant.id



    def show_plot(self,plot_name):

        return Image(filename=self.work_dir + '/' + plot_name)


    def clear_cache(self):
        autogen.Completion.clear_cache(self.cache_seed)


    def hello_cmbagent(self):

        return "Hello from cmbagent!"




    def filter_and_combine_agent_names(self, input_list):
        # Filter the input list to include only entries in self.agent_names
        filtered_list = [item for item in input_list if item in self.agent_names]

        # Convert the filtered list of strings into one string
        combined_string = ', '.join(filtered_list)

        return combined_string


    def set_planner_instructions(self):

        # available agents and their roles:
        available_agents = "\n\n#### Available agents and their roles\n\n"
        
        for agent in self.agents:

            if agent.name in ['planner', 'engineer', 'executor', 'admin']:
                continue


            if 'description' in agent.info:

                role = agent.info['description']

            else:

                role = agent.info['instructions']

            available_agents += f"- *{agent.name}* : {role}\n"


        # collect allowed transitions
        all_allowed_transitions = "\n\n#### Allowed transitions\n\n"

        for agent in self.agents:

            all_allowed_transitions += f"\t- {agent.name} -> {self.filter_and_combine_agent_names(agent.info['allowed_transitions'])}\n"



        # commenting for now
        # self.planner.info['instructions'] += available_agents + '\n\n' #+ all_allowed_transitions

        return


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



