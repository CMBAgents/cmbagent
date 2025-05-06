import os
from cmbagent.base_agent import BaseAgent



class ClassyAgent(BaseAgent):

    def __init__(self, llm_config=None, **kwargs):

        agent_id = os.path.splitext(os.path.abspath(__file__))[0]

        super().__init__(llm_config=llm_config, agent_id=agent_id, **kwargs)




    def set_agent(self,**kwargs):

        super().set_gpt_assistant_agent(**kwargs)


