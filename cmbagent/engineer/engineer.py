from cmbagent.utils import *
from cmbagent.assistants.base_agent import BaseAgent

logger = logging.getLogger(__name__)

class EngineerAgent(BaseAgent):

    def __init__(self, llm_config=None, **kwargs):

        agent_id = os.path.splitext(os.path.abspath(__file__))[0]

        super().__init__(llm_config=llm_config, agent_id=agent_id, **kwargs)


    def set_agent(self):

        self.agent = AssistantAgent(
            name= self.info["name"],
            system_message= self.info["instructions"],
            description=self.info["description"],
            llm_config=self.llm_config,
        )


