import os
from cmbagent.base_agent import BaseAgent
from pydantic import BaseModel, Field
from cmbagent.utils import default_llm_config_list
from autogen.cmbagent_utils import cmbagent_debug


class ResearcherResponseFormatterAgent(BaseAgent):
    
    def __init__(self, llm_config=None, **kwargs):

        agent_id = os.path.splitext(os.path.abspath(__file__))[0]

        llm_config['config_list'][0]['response_format'] = self.StructuredMardown

        super().__init__(llm_config=llm_config, agent_id=agent_id, **kwargs)


    def set_agent(self,**kwargs):

        super().set_assistant_agent(**kwargs)


    class StructuredMardown(BaseModel):
        markdown_block: str = Field(..., description="The Mardown notes in a form ready to saved. Without spurious indentation at the start.")
        filename: str = Field(..., description="The name to give to this markdown notes in the format: <filename>.md")
        # relative_path: Optional[str] = Field(None, description="The relative path to the file (exclude <filename>.md itself)")

        def format(self) -> str:
            full_path = self.filename
            comment_line = f"<!-- filename: {full_path} -->"
            lines = self.markdown_block.splitlines()
        
            if lines and lines[0].strip().startswith("<!-- filename:"):
                # Replace the existing filename comment with the new one.
                lines[0] = comment_line
                updated_markdown_block = "\n".join(lines)
            else:
                # Prepend the new filename comment.
                updated_markdown_block = "\n".join([comment_line, self.markdown_block])
        
            return (
f"**Markdown:**\n\n"
f"```markdown\n"
f"{updated_markdown_block}\n"
f"```"
            )








