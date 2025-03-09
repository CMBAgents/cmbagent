import os
from cmbagent.base_agent import BaseAgent
from pydantic import BaseModel, Field
from typing import List


class ReviewerResponseFormatterAgent(BaseAgent):
    
    def __init__(self, llm_config=None, **kwargs):

        agent_id = os.path.splitext(os.path.abspath(__file__))[0]

        llm_config['config_list'][0]['response_format'] = self.PlanReviewerResponse

        super().__init__(llm_config=llm_config, agent_id=agent_id, **kwargs)


    def set_agent(self,**kwargs):

        super().set_assistant_agent(**kwargs)



    class PlanReviewerResponse(BaseModel):
        recommendations: List[str] = Field(..., description="recommendation.")
        def format(self) -> str:
            recommendations_output = "\n".join(f"- {recommendation}\n" for i, recommendation in enumerate(self.recommendations))
            return f"""
**Recommendations:**

{recommendations_output}
            """
