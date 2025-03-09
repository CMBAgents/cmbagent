import os
from cmbagent.base_agent import BaseAgent
from pydantic import BaseModel, Field
from typing import Optional


class EngineerResponseFormatterAgent(BaseAgent):
    
    def __init__(self, llm_config=None, **kwargs):

        agent_id = os.path.splitext(os.path.abspath(__file__))[0]

        llm_config['config_list'][0]['response_format'] = self.EngineerResponse

        super().__init__(llm_config=llm_config, agent_id=agent_id, **kwargs)


    def set_agent(self,**kwargs):

        super().set_assistant_agent(**kwargs)





    class EngineerResponse(BaseModel):
        # steps: list[Step]
        # step: Step
        code_explanation: str = Field(..., description="Explanation of the Python code")
        python_code: str = Field(..., description="The Python code in a form ready to execute")
        filename: str = Field(..., description="The name to give to this Python script")
        relative_path: Optional[str] = Field(None, description="The relative path to the file (exclude <filename>.py itself)")

        def format(self) -> str:
            final_filename = self.filename if self.filename.endswith(".py") else self.filename + ".py"
            
            if self.relative_path:
                # Remove any trailing slashes from relative_path to avoid double slashes
                cleaned_path = self.relative_path.rstrip("/\\")
                full_path = os.path.join(cleaned_path, final_filename)
            else:
                full_path = final_filename
        
            comment_line = f"# filename: {full_path}"
            code_lines = self.python_code.splitlines()
            
            if code_lines and code_lines[0].strip().startswith("# filename:"):
                # Replace the first line with the updated filename comment.
                code_lines[0] = comment_line
                updated_python_code = "\n".join(code_lines)
            else:
                # Prepend the new filename comment.
                updated_python_code = "\n".join([comment_line, self.python_code])
        
            return (
f"**Code Explanation:**\n\n"
f"{self.code_explanation}\n\n"
f"**Python Code:**\n\n"
f"```python\n"
f"{updated_python_code}\n"
f"```"
            )



