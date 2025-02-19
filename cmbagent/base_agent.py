import os 
import logging
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple, Union
from cmbagent.utils import yaml_load_file,GPTAssistantAgent,AssistantAgent,UserProxyAgent,LocalCommandLineCodeExecutor,GroupChat,default_groupchat_intro_message,file_search_max_num_results
import sys
from autogen import Agent, SwarmAgent, ConversableAgent


class CmbAgentUserProxyAgent(UserProxyAgent): ### this is for admin and executor 
    """A custom proxy agent for the user with redefined default descriptions."""

    # Override the default descriptions
    DEFAULT_USER_PROXY_AGENT_DESCRIPTIONS = {
        "ALWAYS": "An attentive HUMAN user who can answer questions about the task and provide feedback.", # default for admin 
        "TERMINATE": "A user that can run Python code and report back the execution results.",
        "NEVER": "A computer terminal that performs no other action than running Python scripts (provided to it quoted in ```python code blocks).", # default for executor 
    }


class CmbAgentGroupChat(GroupChat):
    def __init__(
        self,
        agents: List[Agent],
        rag_agents: List[Agent],
        messages: List,
        speaker_selection_method: str,
        max_round: int,
        send_introductions: bool,
        admin_name: str,
        select_speaker_auto_verbose: Optional[bool] = True,
        speaker_transitions_type: Optional[str] = None,
        select_speaker_prompt_template: Optional[str] = None,
        select_speaker_message_template: Optional[str] = None,
        agent_type: Optional[str] = None,   
        allowed_or_disallowed_speaker_transitions: Optional[List] = None,
        cost: int = 0,
        verbose: bool = False,
    ):

        if agent_type == 'swarm':
            # Initialize the parent GroupChat
            super().__init__(
                agents = agents,
                messages = messages,
                max_round=max_round,
                speaker_selection_method=speaker_selection_method,
                send_introductions=send_introductions,
                admin_name=admin_name,
                select_speaker_auto_verbose=select_speaker_auto_verbose,
            )
        else:
            # Initialize the parent GroupChat
            super().__init__(
                agents=agents,
                allowed_or_disallowed_speaker_transitions=allowed_or_disallowed_speaker_transitions,
                speaker_transitions_type=speaker_transitions_type,
                messages=messages,
                speaker_selection_method=speaker_selection_method,
                max_round=max_round,
                select_speaker_auto_verbose=select_speaker_auto_verbose,
                send_introductions=send_introductions,
                admin_name=admin_name,
                select_speaker_prompt_template=select_speaker_prompt_template,
                select_speaker_message_template=select_speaker_message_template,
            )
        
        # Initialize CmbAgentGroupChat-specific attributes
        self.rag_agents = rag_agents
        self.verbose = verbose
        self.cost = cost
        self.DEFAULT_INTRO_MSG = default_groupchat_intro_message



class BaseAgent:

    def __init__(self, 
                 llm_config=None,
                 agent_id=None,
                 work_dir=None,
                 agent_type=None,
                 **kwargs):
        

        self.kwargs = kwargs

        self.llm_config = llm_config

        self.info = yaml_load_file(agent_id + ".yaml")

        self.name = self.info["name"]

        self.work_dir = work_dir

        self.agent_type = agent_type
        



    def set_agent(self,
                  instructions=None, 
                  description=None,
                  vector_store_ids=None, 
                  agent_temperature=None, 
                  agent_top_p=None):

    
        print('setting agent: ',self.name)
        # print(self.info['assistant_config']['tool_resources']['file_search'])
        # print()    
        if instructions is not None:

            self.info["instructions"] = instructions

        if description is not None:

            self.info["description"] = description

        if vector_store_ids is not None:

            self.info['assistant_config']['tool_resources']['file_search']['vector_store_ids'] = [vector_store_ids]
        
            

        if agent_temperature is not None:

            self.info['assistant_config']['temperature'] = agent_temperature

        if agent_top_p is not None:

            self.info['assistant_config']['top_p'] = agent_top_p

        
        # dir_path = os.path.dirname(os.path.realpath(__file__))
        dir_path = os.getenv('CMBAGENT_DATA')
        data_path = os.path.join(dir_path, 'data', self.name.replace('_agent', ''))
        # List files in the data_path excluding unwanted files
        files = [f for f in os.listdir(data_path) if not (f.startswith('.') or f.endswith('.ipynb') or f.endswith('.yaml') or f.endswith('.txt') or os.path.isdir(os.path.join(data_path, f)))]

        # cmbagent debug
        print("\n adding files to instructions: ", files)
        self.info["instructions"] += f'\n You have access to the following files: {files}.\n'


        logger = logging.getLogger(self.name) 
        logger.info("Loaded assistant info:")

        for key, value in self.info.items():

            logger.info(f"{key}: {value}")

        # if self.agent_type == 'swarm':

        #     self.agent = CmbAgentSwarmAgent(
        #         name=self.name,
        #         system_message= self.info["instructions"],
        #         description=self.info["description"],
        #         llm_config=self.llm_config,
        #         )

        #     ### case of missing vector store not implemented for swarm...
        #     ### TODO: implement this, see below. 

        # else:
        self.info['assistant_config']['tools'][0]['file_search'] ={'max_num_results': file_search_max_num_results} 
        self.agent = GPTAssistantAgent(
            name=self.name,
            instructions= self.info["instructions"],
            description=self.info["description"],
            assistant_config=self.info["assistant_config"],
            llm_config=self.llm_config,
            overwrite_tools=True,
            overwrite_instructions=True
            )

        # if self.agent._assistant_error is not None:

        #     # print(self.agent._assistant_error)
        #     if "No vector store" in self.agent._assistant_error:
        #         print(f"Vector store not found for {self.name}")
        #         print(f"re-instantiating with make_vector_stores=['{self.name.rstrip('_agent')}'],")
                
        #         return 1


    ## for engineer agent
    def set_assistant_agent(self,
                            instructions=None, 
                            description=None):

        if instructions is not None:

            self.info["instructions"] = instructions

        if description is not None:

            self.info["description"] = description

        logger = logging.getLogger(self.name) 
        logger.info("Loaded assistant info:")
        for key, value in self.info.items():
            logger.info(f"{key}: {value}")

        # print('setting assistant agent: ',self.name)
        # print('self.agent_type: ',self.agent_type)

        if self.agent_type == 'swarm':

            self.agent = CmbAgentSwarmAgent(
                name=self.name,
                system_message=self.info["instructions"],
                description=self.info["description"],
                llm_config=self.llm_config,
                )

        else:

            self.agent = AssistantAgent(
                name= self.name,
                system_message= self.info["instructions"],
                description=self.info["description"],
                llm_config=self.llm_config,
            )

        ## cmbagent modif print to help debug: 
        ## print('in cmbagent/base_agent.py self.agent: ',self.agent)
        ## print('in cmbagent/base_agent.py self.agent.llm_config: ',self.agent.llm_config)

    def set_code_agent(self,instructions=None):

        if instructions is not None:
            self.info["instructions"] = instructions

        logger = logging.getLogger(self.name) 
        logger.info("Loaded assistant info:")
        for key, value in self.info.items():
            logger.info(f"{key}: {value}")

        if self.agent_type == 'swarm':
            self.agent = CmbAgentSwarmAgent(
                name= self.name,
                system_message= self.info["instructions"],
                description=self.info["description"],
                llm_config=self.llm_config,
                human_input_mode=self.info["human_input_mode"],
            max_consecutive_auto_reply=self.info["max_consecutive_auto_reply"],
            is_termination_msg=lambda x: x.get("content", "").rstrip().endswith("TERMINATE"),
            code_execution_config={
                "executor": LocalCommandLineCodeExecutor(work_dir=self.work_dir,
                                                         timeout=self.info["timeout"],
                                                         execution_policies = {
                                                            "python": True,
                                                            "bash": False,
                                                            "shell": False,
                                                            "sh": False,
                                                            "pwsh": False,
                                                            "powershell": False,
                                                            "ps1": False,
                                                            "javascript": False,
                                                            "html": False,
                                                            "css": False,
                                                         }
                                                         ),
                "last_n_messages": 3,
            },
        )

        else:

            self.agent = CmbAgentUserProxyAgent(
                name= self.name,
                system_message= self.info["instructions"],
                description=self.info["description"],
                llm_config=self.llm_config,
                human_input_mode=self.info["human_input_mode"],
            max_consecutive_auto_reply=self.info["max_consecutive_auto_reply"],
            is_termination_msg=lambda x: x.get("content", "").rstrip().endswith("TERMINATE"),
            code_execution_config={
                "executor": LocalCommandLineCodeExecutor(work_dir=self.work_dir,
                                                         timeout=self.info["timeout"],
                                                         execution_policies = {
                                                            "python": True,
                                                            "bash": True,
                                                            "shell": False,
                                                            "sh": False,
                                                            "pwsh": False,
                                                            "powershell": False,
                                                            "ps1": False,
                                                            "javascript": False,
                                                            "html": False,
                                                            "css": False,
                                                         }
                                                         ),
                "last_n_messages": 2,
            },
        )

    def set_admin_agent(self,instructions=None):

        logger = logging.getLogger(self.name) 
        logger.info("Loaded assistant info:")

        for key, value in self.info.items():

            logger.info(f"{key}: {value}")

        self.agent = CmbAgentUserProxyAgent(
            name= self.name,
            system_message= self.info["instructions"],
            code_execution_config=self.info["code_execution_config"],
        )



class CmbAgentSwarmAgent(ConversableAgent):
    """CMB Swarm agent for participating in a swarm.

    CmbAgentSwarmAgent is a subclass of SwarmAgent, which is a subclass of ConversableAgent.

    Additional args:
        functions (List[Callable]): A list of functions to register with the agent.
    """
    pass