import os
from cmbagent.base_agent import BaseAgent
from pydantic import BaseModel, Field
from typing import Optional, List

class ClassySzResponseFormatterAgent(BaseAgent):
    
    def __init__(self, llm_config=None, **kwargs):

        agent_id = os.path.splitext(os.path.abspath(__file__))[0]

        llm_config['config_list'][0]['response_format'] = self.ClassySzResponse

        super().__init__(llm_config=llm_config, agent_id=agent_id, **kwargs)


    def set_agent(self,**kwargs):

        super().set_assistant_agent(**kwargs)



    class PythonCode(BaseModel):
        code: Optional[str] = Field(None, description="The draft of the Python code needed for classy_sz")

    class ClassySzResponse(BaseModel):

        file_search_task: str = Field(
            ...,
            description="Details of the file search task."
        )
        file_consulted: List[str] = Field(
            ...,
            description="List of files consulted during the task."
        )
        results: str = Field(
            ...,
            description="Results of the file search."
        )
        
        python_code: "ClassySzResponseFormatterAgent.PythonCode" = Field(..., description="Python code snippet related to the task.")

        def format(self) -> str:
            # Format the list of consulted files as a bullet list.
            consulted_files = "\n".join(f"- {file}" for file in self.file_consulted)
            code_text = self.python_code.code or "No code provided."
            return (
                f"**File Search Task:**\n\n{self.file_search_task}\n\n"
                f"**Files Consulted:**\n{consulted_files}\n\n"
                f"**Results:**\n{self.results}\n\n"
                f"**Rough Python Code:**\n\n```python\n{code_text}\n```"
            )








