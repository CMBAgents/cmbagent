import os
from cmbagent.base_agent import BaseAgent
from pydantic import BaseModel, Field
from typing import List

class AaaiKeywordsFinderAgent(BaseAgent):
    
    def __init__(self, llm_config=None, **kwargs):

        agent_id = os.path.splitext(os.path.abspath(__file__))[0]

        llm_config['config_list'][0]['response_format'] = self.AaaiKeywordsResponse

        super().__init__(llm_config=llm_config, agent_id=agent_id, **kwargs)


    def set_agent(self,**kwargs):

        super().set_assistant_agent(**kwargs)

    class AaaiKeywordsResponse(BaseModel):

        results: List[str] = Field(
            ...,
            description="Results of the keyword search.",
            example=["neural network", "transformer", "self-attention"]
        )
        

        def format(self) -> str:    #
            keywords = "\n".join(f"-{keyword}" for keyword in self.results)

            return (
                f"Keywords:\n{keywords}\n"
            )







