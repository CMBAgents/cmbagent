import os
from cmbagent.base_agent import BaseAgent
from pydantic import BaseModel, Field
from typing import List, Optional, Literal


class Subtasks(BaseModel):
    sub_task: str = Field(..., description="The sub-task to be performed")
    sub_task_agent: Literal["engineer", "researcher", "perplexity", "idea_maker", "idea_hater", "classy_sz_agent"] =  Field(..., description="The name of the agent in charge of the sub-task")
    bullet_points: List[str] = Field(
        ..., description="A list of bullet points explaining what the sub-task should do"
    )

class PlannerResponse(BaseModel):
    # main_task: str = Field(..., description="The exact main task to solve.")
    sub_tasks: List[Subtasks]

    def format(self) -> str:
        plan_output = ""
        for i, step in enumerate(self.sub_tasks):
            plan_output += f"\n- Step {i + 1}:\n\t* sub-task: {step.sub_task}\n\t* agent in charge: {step.sub_task_agent}\n"
            if step.bullet_points:
                plan_output += f"\n\t* instructions:\n"
                for bullet in step.bullet_points:
                    plan_output += f"\t\t- {bullet}\n"
        message = f"""
**PLAN**
{plan_output}
        """
        return message
        

class PlannerResponseFormatterAgent(BaseAgent):
    
    def __init__(self, llm_config=None, **kwargs):

        agent_id = os.path.splitext(os.path.abspath(__file__))[0]

        llm_config['config_list'][0]['response_format'] = PlannerResponse

        super().__init__(llm_config=llm_config, agent_id=agent_id, **kwargs)


    def set_agent(self,**kwargs):

        super().set_assistant_agent(**kwargs)






