import os 
import logging

from cmbagent.utils import yaml_load_file,GPTAssistantAgent,AssistantAgent,UserProxyAgent,LocalCommandLineCodeExecutor,work_dir,GroupChat,default_groupchat_intro_message
import sys

class CmbAgentUserProxyAgent(UserProxyAgent): ### this is for admin and executor 
    """A custom proxy agent for the user with redefined default descriptions."""

    # Override the default descriptions
    DEFAULT_USER_PROXY_AGENT_DESCRIPTIONS = {
        "ALWAYS": "An attentive HUMAN user who can answer questions about the task and provide feedback.", # default for admin 
        "TERMINATE": "A user that can run Python code and report back the execution results.",
        "NEVER": "A computer terminal that performs no other action than running Python scripts (provided to it quoted in ```python code blocks).", # default for executor 
    }

class CmbAgentGroupChat(GroupChat):
    cost = 0
    # DEFAULT_INTRO_MSG = default_groupchat_intro_message



class BaseAgent:

    def __init__(self, 
                 llm_config=None,
                 agent_id=None,
                 **kwargs):
        

        self.kwargs = kwargs

        self.llm_config = llm_config

        self.info = yaml_load_file(agent_id + ".yaml")

        self.name = self.info["name"]
        



    def set_agent(self,instructions=None, description=None,
                  vector_store_ids=None, agent_temperature=None, 
                  agent_top_p=None):

    
        # print('setting agent: ',self.name)
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

        
        dir_path = os.path.dirname(os.path.realpath(__file__))
        data_path = os.path.join(dir_path, 'data', self.name.replace('_agent', ''))
        # List files in the data_path excluding unwanted files
        files = [f for f in os.listdir(data_path) if not (f.startswith('.') or f.endswith('.ipynb') or f.endswith('.yaml') or f.endswith('.txt') or os.path.isdir(os.path.join(data_path, f)))]

        self.info["instructions"] += f'\n You have access to the following files: {files}.\n'


        logger = logging.getLogger(self.name) 
        logger.info("Loaded assistant info:")

        for key, value in self.info.items():

            logger.info(f"{key}: {value}")


        self.agent = GPTAssistantAgent(
            name=self.name,
            instructions= self.info["instructions"],
            description=self.info["description"],
            assistant_config=self.info["assistant_config"],
            llm_config=self.llm_config,
            overwrite_tools=True,
            overwrite_instructions=True
        )

        if self.agent._assistant_error is not None:

            # print(self.agent._assistant_error)
            if "No vector store" in self.agent._assistant_error:
                print(f"Vector store not found for {self.name}")
                print(f"re-instantiating with make_vector_stores=['{self.name.rstrip('_agent')}'],")
                
                return 1



    def set_assistant_agent(self,instructions=None, description=None):

        if instructions is not None:

            self.info["instructions"] = instructions

        if description is not None:

            self.info["description"] = description

        logger = logging.getLogger(self.name) 
        logger.info("Loaded assistant info:")

        for key, value in self.info.items():

            logger.info(f"{key}: {value}")

        self.agent = AssistantAgent(
            name= self.name,
            system_message= self.info["instructions"],
            description=self.info["description"],
            llm_config=self.llm_config,
        )


    def set_code_agent(self,instructions=None):

        logger = logging.getLogger(self.name) 
        logger.info("Loaded assistant info:")

        for key, value in self.info.items():

            logger.info(f"{key}: {value}")


        self.agent = CmbAgentUserProxyAgent(
            name= self.name,
            system_message= self.info["instructions"],
            description=self.info["description"],
            llm_config=self.llm_config,
            human_input_mode=self.info["human_input_mode"],
            max_consecutive_auto_reply=self.info["max_consecutive_auto_reply"],
            is_termination_msg=lambda x: x.get("content", "").rstrip().endswith("TERMINATE"),
            code_execution_config={
                "executor": LocalCommandLineCodeExecutor(work_dir=work_dir,
                                                         timeout=self.info["timeout"]),
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