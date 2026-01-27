import os
import logging
from cmbagent.utils.yaml import yaml_load_file
from autogen.coding import LocalCommandLineCodeExecutor
from autogen.agentchat import UserProxyAgent
from autogen.agentchat import ConversableAgent, UpdateSystemMessage
import autogen
import copy

# cmbagent_debug=True

cmbagent_debug = autogen.cmbagent_utils.cmbagent_debug

class CmbAgentUserProxyAgent(UserProxyAgent): ### this is for admin and executor
    """A custom proxy agent for the user with redefined default descriptions."""

    # Override the default descriptions
    DEFAULT_USER_PROXY_AGENT_DESCRIPTIONS = {
        "ALWAYS": "An attentive HUMAN user who can answer questions about the task and provide feedback.", # default for admin
        "TERMINATE": "A user that can run Python code and report back the execution results.",
        "NEVER": "A computer terminal that performs no other action than running Python scripts (provided to it quoted in ```python code blocks).", # default for executor
    }


class BaseAgent:

    def __init__(self,
                 llm_config=None,
                 agent_id=None,
                 work_dir=None,
                 agent_type=None,
                 **kwargs):

        self.kwargs = kwargs

        if cmbagent_debug:
            print('\n\n in base_agent.py: __init__: llm_config: ', llm_config)
            print('\n\n')

        self.llm_config = copy.deepcopy(llm_config)

        self.info = yaml_load_file(agent_id + ".yaml")

        self.name = self.info["name"]

        # if self.name == 'idea_maker':
        #     print('idea_maker: ', self.info)
        #     print('llm_config: ', self.llm_config)
        if 'temperature' in self.llm_config['config_list'][0]:
            temperature = self.llm_config['config_list'][0]['temperature']
            self.llm_config['config_list'][0].pop('temperature')
            self.llm_config['temperature'] = temperature
            # print('llm_config: ', self.llm_config)

            # import sys; sys.exit()

        self.work_dir = work_dir

        self.agent_type = agent_type

        if cmbagent_debug:
            print('\n---------------------------------- setting name: ', self.info["name"])
            print('work_dir: ', self.work_dir)
            print('\n----------------------------------')

    def set_agent(self, **kwargs):
        """Auto-detect agent type from YAML config and call appropriate set method.

        Detection logic:
        - Code execution agent: has 'executor' in name and has 'timeout' field
        - Admin/user proxy agent: has 'code_execution_config' == False
        - Standard assistant: default case

        MassGen support:
        - Pass use_massgen=True to enable MassGen for engineer agent

        Remote execution support:
        - Pass custom_executor to use a custom CodeExecutor (e.g., RemoteWebSocketCodeExecutor)
        """
        # Extract custom_executor from kwargs (only used by code execution agents)
        custom_executor = kwargs.pop('custom_executor', None)

        # Check for code execution agent (executor, executor_bash, researcher_executor)
        # Only agents with 'executor' in their name are pure code executors
        if 'executor' in self.name and 'timeout' in self.info:
            self.set_code_agent(custom_executor=custom_executor, **kwargs)

        # Check for admin/user proxy agent
        elif 'code_execution_config' in self.info and self.info['code_execution_config'] is False:
            self.set_admin_agent(**kwargs)

        # Default: standard assistant agent
        else:
            self.set_assistant_agent(**kwargs)

    ## for engineer/.. all non rag agents
    def set_assistant_agent(self,
                            instructions=None,
                            description=None,
                            use_massgen=False,
                            massgen_config=None,
                            massgen_verbose=False,
                            massgen_enable_logging=True,
                            massgen_use_for_retries=False,
                            **kwargs):  # Accept extra kwargs (e.g. custom_executor) to ignore

        if cmbagent_debug:
            print('\n\n\n\nin base_agent.py set_assistant_agent')
            print('name: ',self.name)
            # import sys; sys.exit()

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

        # if self.name == 'plan_setter':
        #     functions = [record_plan_constraints]
        # else:
        #     functions = []

        functions = []

        if self.name == 'cmbagent_tool_executor':
            self.agent = ConversableAgent(
                        name="cmbagent_tool_executor",
                        human_input_mode="NEVER",
                        llm_config=self.llm_config,
                    )

        # Check if MassGen should be used for engineer agent
        elif self.name == 'engineer' and use_massgen:
            try:
                from cmbagent.agents.coding.engineer.massgen_engineer import create_massgen_engineer_agent
            except ImportError as e:
                print(f"[CMBAgent] ERROR: Failed to import MassGen engineer: {e}")
                print(f"[CMBAgent] Falling back to standard engineer agent")
                # Fall back to standard agent
                self.agent = CmbAgentSwarmAgent(
                    name=self.name,
                    update_agent_state_before_reply=[UpdateSystemMessage(self.info["instructions"]),],
                    description=self.info.get("description", f"Agent {self.name}"),
                    llm_config=self.llm_config,
                    cmbagent_debug=cmbagent_debug,
                    functions=functions,
                )
            else:
                print(f"[CMBAgent] Creating MassGen engineer agent (hybrid mode)")
                self.agent = create_massgen_engineer_agent(
                    name=self.name,
                    llm_config=self.llm_config,
                    instructions=self.info["instructions"],
                    description=self.info.get("description", f"Agent {self.name}"),
                    massgen_config=massgen_config,
                    verbose=massgen_verbose,
                    enable_logging=massgen_enable_logging,
                    use_massgen_for_retries=massgen_use_for_retries,
                    cmbagent_debug=cmbagent_debug,
                    functions=functions,
                )

        else:
            self.agent = CmbAgentSwarmAgent(
                name=self.name,
                # system_message=self.info["instructions"],
                update_agent_state_before_reply=[UpdateSystemMessage(self.info["instructions"]),],
                description=self.info.get("description", f"Agent {self.name}"),
                llm_config=self.llm_config,
            cmbagent_debug=cmbagent_debug,
            functions=functions,
            )
        


        if cmbagent_debug:
            print("AssistantAgent set.... moving on.\n")

    def set_code_agent(self, instructions=None, custom_executor=None):
        """
        Set up a code execution agent.

        Args:
            instructions: Optional custom instructions for the agent
            custom_executor: Optional custom CodeExecutor (e.g., RemoteWebSocketCodeExecutor).
                           If not provided, uses LocalCommandLineCodeExecutor.
        """

        if instructions is not None:
            self.info["instructions"] = instructions

        logger = logging.getLogger(self.name)
        logger.info("Loaded assistant info:")
        for key, value in self.info.items():
            logger.info(f"{key}: {value}")

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

        if 'bash' in self.name:
            execution_policies = {
                "python": False,
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

        # Use custom executor if provided, otherwise use LocalCommandLineCodeExecutor
        if custom_executor is not None:
            executor = custom_executor
            if cmbagent_debug:
                print(f'Using custom executor: {type(executor).__name__}')
        else:
            executor = LocalCommandLineCodeExecutor(
                work_dir=self.work_dir,
                timeout=self.info["timeout"],
                execution_policies=execution_policies
            )

        self.agent = CmbAgentUserProxyAgent(
            name= self.name,
            system_message= self.info["instructions"],
            description=self.info.get("description", f"Agent {self.name}"),
            llm_config=False,  # Code execution agents don't need LLM
            human_input_mode=self.info["human_input_mode"],
        max_consecutive_auto_reply=self.info["max_consecutive_auto_reply"],
        is_termination_msg=lambda x: x.get("content", "").rstrip().endswith("TERMINATE"),
        code_execution_config={
            "executor": executor,
            "last_n_messages": 2,
        },
        )

        if cmbagent_debug:
            print('code_agent set with work_dir: ', self.work_dir, '.... moving on.\n')




    def set_admin_agent(self, instructions=None, **kwargs):  # Accept extra kwargs to ignore

        logger = logging.getLogger(self.name)
        logger.info("Loaded assistant info:")

        for key, value in self.info.items():

            logger.info(f"{key}: {value}")

        self.agent = CmbAgentUserProxyAgent(
            name= self.name,
            update_agent_state_before_reply=[UpdateSystemMessage(self.info["instructions"]),],
            # system_message= self.info["instructions"],
            code_execution_config=self.info["code_execution_config"],
        )



class CmbAgentSwarmAgent(ConversableAgent):
    """CMB Swarm agent for participating in a swarm.

    CmbAgentSwarmAgent is a subclass of SwarmAgent, which is a subclass of ConversableAgent.

    Additional args:
        functions (List[Callable]): A list of functions to register with the agent.
    """
    pass