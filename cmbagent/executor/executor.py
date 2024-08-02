from cmbagent.utils import *
from cmbagent.assistants.base_agent import BaseAgent

logger = logging.getLogger(__name__)

class ExecutorAgent(BaseAgent):
    
    def __init__(self, llm_config=None, **kwargs):

        agent_id = os.path.splitext(os.path.abspath(__file__))[0]

        super().__init__(llm_config=llm_config, agent_id=agent_id, **kwargs)

    def set_agent(self):

        self.agent = UserProxyAgent(
            name= self.info["name"],
            system_message= self.info["instructions"],
            description=self.info["description"],
            llm_config=self.llm_config,
            human_input_mode=self.info["human_input_mode"],
            max_consecutive_auto_reply=self.info["max_consecutive_auto_reply"],
            is_termination_msg=lambda x: x.get("content", "").rstrip().endswith("TERMINATE"),
            code_execution_config={
                "executor": LocalCommandLineCodeExecutor(work_dir=work_dir,
                                                         timeout = self.info["timeout"]),
            },
        )





