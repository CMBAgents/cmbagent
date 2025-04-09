import os
from cmbagent.base_agent import BaseAgent
from pydantic import BaseModel, Field
from cmbagent.utils import default_llm_config_list
from autogen.cmbagent_utils import cmbagent_debug
import re


class ResearcherResponseFormatterAgent(BaseAgent):
    
    def __init__(self, llm_config=None, **kwargs):

        agent_id = os.path.splitext(os.path.abspath(__file__))[0]

        llm_config['config_list'][0]['response_format'] = self.StructuredMardown

        super().__init__(llm_config=llm_config, agent_id=agent_id, **kwargs)


    def set_agent(self,**kwargs):

        super().set_assistant_agent(**kwargs)


    class StructuredMardown(BaseModel):
        markdown_block: str = Field(..., description="A Markdown block containing the researcher's notes in a form ready to be saved. Should not contain ```markdown fences.")
        filename: str = Field(..., description="The name to give to this markdown notes in the format: <filename>.md")

        def format(self) -> str:
            full_path = self.filename
            comment_line = f"<!-- filename: {full_path} -->"

            # Step 1: Remove any leading or trailing markdown code fences
            cleaned_block = re.sub(r"^\s*```(?:markdown)?\s*", "", self.markdown_block.strip(), flags=re.IGNORECASE)
            cleaned_block = re.sub(r"\s*```\s*$", "", cleaned_block, flags=re.IGNORECASE)

            lines = cleaned_block.splitlines()
            
            # Step 2: Replace or prepend the comment line
            if lines and lines[0].strip().startswith("<!-- filename:"):
                lines[0] = comment_line
            else:
                lines = [comment_line] + lines

            updated_markdown_block = "\n".join(lines)

            # Step 3: Wrap clean block in a single markdown code fence
            return (
    f"**Markdown:**\n\n"
    f"```markdown\n"
    f"{updated_markdown_block}\n"
    f"```"
            )

#     class StructuredMardown(BaseModel):
#         markdown_block: str = Field(..., description="The Mardown notes in a form ready to saved. Without spurious indentation at the start. It should not start with ```markdown, as it will be added automatically.")
#         filename: str = Field(..., description="The name to give to this markdown notes in the format: <filename>.md")
#         # relative_path: Optional[str] = Field(None, description="The relative path to the file (exclude <filename>.md itself)")

#         def format(self) -> str:
#             full_path = self.filename
#             comment_line = f"<!-- filename: {full_path} -->"
#             lines = self.markdown_block.splitlines()
        
#             if lines and lines[0].strip().startswith("<!-- filename:"):
#                 # Replace the existing filename comment with the new one.
#                 lines[0] = comment_line
#                 updated_markdown_block = "\n".join(lines)
#             else:
#                 # Prepend the new filename comment.
#                 updated_markdown_block = "\n".join([comment_line, self.markdown_block])
        
#             return (
# f"**Markdown:**\n\n"
# f"```markdown\n"
# f"{updated_markdown_block}\n"
# f"```"
#             )








