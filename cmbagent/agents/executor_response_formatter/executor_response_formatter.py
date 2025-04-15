import os
from cmbagent.base_agent import BaseAgent
from pydantic import BaseModel, Field
from typing import List, Optional, Literal




class ExecutorResponseFormatterAgent(BaseAgent):
    
    def __init__(self, llm_config=None, **kwargs):

        agent_id = os.path.splitext(os.path.abspath(__file__))[0]

        llm_config['config_list'][0]['response_format'] = self.ExecutorResponse

        super().__init__(llm_config=llm_config, agent_id=agent_id, **kwargs)


    def set_agent(self,**kwargs):

        super().set_assistant_agent(**kwargs)



    class ExecutorResponse(BaseModel):
        execution_summary: str = Field(
            ..., description="Summay of the Execution output"
        )
        execution_status: Literal["success", "failure"] = Field(
            ..., description="Status of the execution."
        )
        next_agent_suggestion: Literal["engineer", "classy_sz_agent", "control", "installer","camb_agent","cobaya_agent"] = Field(
            None, ## default value
            description=r"""
            Suggestion for the next agent to call:
            Suggest the engineer agent if error related to generic Python code.
            Suggest the installer agent if error related to missing Python modules (i.e., ModuleNotFoundError).
            Suggest the classy_sz_agent if error is an internal classy_sz error.
            Suggest the camb_agent if error related to internal camb code.
            Suggest the cobaya_agent if error related to internal cobaya code.
            Suggest the control agent if execution was successful.
            """
        )
        current_step_in_plan: int = Field(
            ..., description="Current step in plan."
        )
        def format(self) -> str:
            return f"""
            **Execution Summary:**
            {self.execution_summary}

            **Execution Status:**
            {self.execution_status}

            **Next Agent Suggestion:**
            {self.next_agent_suggestion}

            **Current Step in Plan:**
            {self.current_step_in_plan}
            """







