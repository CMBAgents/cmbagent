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
        filename: str = Field(..., description="The name to give to this Python script")
        relative_path: Optional[str] = Field(
            None, description="The relative path to the file (exclude <filename>.py itself)"
        )
        code_explanation: str = Field(
            ..., description="Copy of the engineer's explanation of the Python code provided. Including the docstrings of the methods used."
        )
        modification_summary: Optional[str] = Field(
            None,
            description="Copy of the engineer's summary of any modifications made to fix errors from the previous version."
        )
        python_code: str = Field(
            ..., description="Copy of the engineer's Python code in a form ready to execute. Should not contain anything else than code."
        )

        def format(self) -> str:
            final_filename = self.filename if self.filename.endswith(".py") else self.filename + ".py"

            if self.relative_path:
                cleaned_path = self.relative_path.rstrip("/\\")
                full_path = os.path.join(cleaned_path, os.path.basename(final_filename))
            else:
                full_path = final_filename

            comment_line = f"# filename: {full_path}"
            code_lines = self.python_code.splitlines()

            if code_lines and code_lines[0].strip().startswith("# filename:"):
                code_lines[0] = comment_line
                updated_python_code = "\n".join(code_lines)
            else:
                updated_python_code = "\n".join([comment_line, self.python_code])

            response_parts = [f"**Code Explanation:**\n\n{self.code_explanation}"]

            if self.modification_summary:
                response_parts.append(
                    f"**Modifications:**\n\n{self.modification_summary}"
                )

            response_parts.append(
                f"**Python Code:**\n\n```python\n{updated_python_code}\n```"
            )

            return "\n\n".join(response_parts)


