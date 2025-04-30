import os
from cmbagent.base_agent import BaseAgent
from pydantic import BaseModel, Field
from typing import List, Optional


class Subtasks(BaseModel):
    idea_description: str = Field(..., description="The description of the idea")
    bullet_points: List[str] = Field(
        ..., description="A list of bullet points explaining what the idea is"
    )

class IdeaMakerResponse(BaseModel):
    # main_task: str = Field(..., description="The exact main task to solve.")
    sub_tasks: List[Subtasks]

    def format(self) -> str:
        plan_output = ""
        for i, step in enumerate(self.sub_tasks):
            plan_output += f"\n- Idea {i + 1}:\n\t* {step.idea_description}\n"
            if step.bullet_points:
                # plan_output += f"\n\t* bullet points:\n"
                for bullet in step.bullet_points:
                    plan_output += f"\t\t- {bullet}\n"
        message = f"""
**Ideas**
{plan_output}
        """
        return message
        

class IdeaMakerResponseFormatterAgent(BaseAgent):
    
    def __init__(self, llm_config=None, **kwargs):

        agent_id = os.path.splitext(os.path.abspath(__file__))[0]

        llm_config['config_list'][0]['response_format'] = IdeaMakerResponse

        super().__init__(llm_config=llm_config, agent_id=agent_id, **kwargs)


    def set_agent(self,**kwargs):

        super().set_assistant_agent(**kwargs)






