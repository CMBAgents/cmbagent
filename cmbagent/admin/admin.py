from cmbagent.utils import *
from cmbagent.assistants.base_agent import BaseAgent

logger = logging.getLogger(__name__)

class AdminAgent(BaseAgent):
    
    def __init__(self, llm_config=None, **kwargs):

        agent_id = os.path.splitext(os.path.abspath(__file__))[0]

        super().__init__(llm_config=llm_config, agent_id=agent_id, **kwargs)


    def set_agent(self):

        self.agent = autogen.UserProxyAgent(
            name= self.info["name"],
            system_message= self.info["instructions"],
            code_execution_config=self.info["code_execution_config"],
        )





