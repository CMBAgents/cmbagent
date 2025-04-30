import os
from cmbagent.base_agent import BaseAgent
from pydantic import BaseModel, Field
from typing import Optional, List

class CambResponseFormatterAgent(BaseAgent):
    
    def __init__(self, llm_config=None, **kwargs):

        agent_id = os.path.splitext(os.path.abspath(__file__))[0]

        llm_config['config_list'][0]['response_format'] = self.CambResponse

        super().__init__(llm_config=llm_config, agent_id=agent_id, **kwargs)


    def set_agent(self,**kwargs):

        super().set_assistant_agent(**kwargs)



    class PythonCode(BaseModel):
        code: Optional[str] = Field(None, description="The draft of the Python code needed for camb")

    class CambResponse(BaseModel):

        file_search_task: str = Field(
            ...,
            description="Details of the file search task."
        )
        file_consulted: List[str] = Field(
            ...,
            description="List of files consulted during the task."
        )
        docstrings: List[str] = Field(
            ...,
            description="List of full complete docstrings of the camb methods needed, not just the function names. These are generally in the documentation in your prompt."
        )
        results: str = Field(
            ...,
            description="Results of the file search. Include the full complete docstrings of the camb methods you used in your response, not just the function names. These are generally in the documentation in your prompt."
        )
        
        python_code: "CambResponseFormatterAgent.PythonCode" = Field(..., description="Python code snippet related to the task (for guidance only).")

        def format(self) -> str:
            # Format the list of consulted files as a bullet list.
            consulted_files = "\n".join(f"- {file}" for file in self.file_consulted)
            code_text = self.python_code.code or "No code provided."
            return (
                f"**File Search Task:**\n\n{self.file_search_task}\n\n"
                f"**Files Consulted:**\n{consulted_files}\n\n"
                f"**Results:**\n{self.results}\n\n"
                f"**Docstrings:**\n{self.docstrings}\n\n"
                f"**Rough Python Code (for guidance only):**\n\n```python\n{code_text}\n```"
            )








