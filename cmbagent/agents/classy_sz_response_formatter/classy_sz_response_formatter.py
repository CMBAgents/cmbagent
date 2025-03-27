import os
from cmbagent.base_agent import BaseAgent
from pydantic import BaseModel, Field
from typing import Optional

class ClassySzResponseFormatterAgent(BaseAgent):
    
    def __init__(self, llm_config=None, **kwargs):

        agent_id = os.path.splitext(os.path.abspath(__file__))[0]

        llm_config['config_list'][0]['response_format'] = self.ClassySzResponse

        super().__init__(llm_config=llm_config, agent_id=agent_id, **kwargs)


    def set_agent(self,**kwargs):

        super().set_assistant_agent(**kwargs)



    class PythonCode(BaseModel):
        code: Optional[str] = Field(None, description="The Python code retrieved or generated")

    class ClassySzResponse(BaseModel):

        file_search_task: str = Field(
            ...,
            description="{retrieval task details}"
        )
        file_consulted: str = Field(
            ...,
            description="{files}"
        )
        results: str = Field(
            ...,
            description="{results of the search}"
        )
        
        python_code: "ClassySzResponseFormatterAgent.PythonCode" = Field(..., description="The Python code block")

        def format(self) -> str:
            python_code = self.python_code.code or "No code provided."
            return f"""
**File Search Task:**

{self.file_search_task}

**File Consulted:**

{self.file_consulted}

**Results:**

{self.results}


**Python Code:**

```python
{python_code}
```
    """








