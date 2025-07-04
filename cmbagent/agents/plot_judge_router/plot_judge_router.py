import os
from cmbagent.base_agent import BaseAgent
from pydantic import BaseModel, Field
from typing import List, Optional, Literal


class PlotJudgeRouterAgent(BaseAgent):
    
    def __init__(self, llm_config=None, **kwargs):

        agent_id = os.path.splitext(os.path.abspath(__file__))[0]

        # llm_config['config_list'][0]['response_format'] = self.PlotJudgeRouterResponse

        super().__init__(llm_config=llm_config, agent_id=agent_id, **kwargs)


    def set_agent(self,**kwargs):

        super().set_assistant_agent(**kwargs)


    # class PlotJudgeRouterResponse(BaseModel):
    #     verdict: Literal["continue", "retry"] = Field(..., description="Final verdict: 'continue' if plot is acceptable, 'retry' if fixes are needed")
    #     problems: Optional[List[str]] = Field(None, description="List of problems found (only if verdict is 'retry')")
    #     fixes: Optional[List[str]] = Field(None, description="List of suggested fixes (only if verdict is 'retry')")

        # def format(self) -> str: