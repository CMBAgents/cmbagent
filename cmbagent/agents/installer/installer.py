import os
from cmbagent.base_agent import BaseAgent
from pydantic import BaseModel, Field

    
class InstallerResponse(BaseModel):
    install_command: str = Field(..., description="The bash command to install the package(s) using pip")

    def format(self) -> str:
        message = f"""
```bash
{self.install_command}
```
        """
        return message

class InstallerAgent(BaseAgent):
    
    def __init__(self, llm_config=None, **kwargs):

        agent_id = os.path.splitext(os.path.abspath(__file__))[0]

        llm_config['config_list'][0]['response_format'] = InstallerResponse

        super().__init__(llm_config=llm_config, agent_id=agent_id, **kwargs)


    def set_agent(self,**kwargs):

        super().set_assistant_agent(**kwargs)








