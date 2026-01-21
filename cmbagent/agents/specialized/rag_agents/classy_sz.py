import os
from cmbagent.base_agent import BaseAgent
from cmbagent.utils import cmbagent_debug

class ClassySzAgent(BaseAgent):

    def __init__(self, llm_config=None, **kwargs):

        if cmbagent_debug:
            print('\n\n in classy_sz.py: __init__: llm_config: ', llm_config)
            print('\n\n')

        agent_id = os.path.splitext(os.path.abspath(__file__))[0]

        super().__init__(llm_config=llm_config, agent_id=agent_id, **kwargs)


    def set_agent(self,**kwargs):

        super().set_gpt_assistant_agent(**kwargs)


