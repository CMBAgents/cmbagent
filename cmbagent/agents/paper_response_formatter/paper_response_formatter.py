import os
from cmbagent.base_agent import BaseAgent
from pydantic import BaseModel, Field
from typing import Optional, List

class PaperResponseFormatterAgent(BaseAgent):

    def __init__(self, llm_config=None, **kwargs):

        agent_id = os.path.splitext(os.path.abspath(__file__))[0]

        # Set the response format to the nested PaperResponse model
        llm_config['config_list'][0]['response_format'] = self.PaperResponse
        
        super().__init__(llm_config=llm_config, agent_id=agent_id, **kwargs)

    def set_agent(self, **kwargs):
        super().set_assistant_agent(**kwargs)

    # Model for extracted data tables
    class DataTable(BaseModel):
        table_caption: Optional[str] = Field(
            None,
            description="Caption or title of the data table"
        )
        table_summary: Optional[str] = Field(
            None,
            description="Brief summary of the table content (detailed content is not necessary)"
        )
        table_content: Optional[str] = Field(
            None,
            description="Full content of the table"
        )

    # The overall response model for papers
    class PaperResponse(BaseModel):
        file_search_task: str = Field(
            ...,
            description="Details of the paper retrieval task"
        )
        file_consulted: str = Field(
            ...,
            description="Files consulted (e.g., PDF or Markdown)"
        )
        results: str = Field(
            ...,
            description="Summary of the extraction results"
        )
        data_tables: Optional[List["PaperResponseFormatterAgent.DataTable"]] = Field(
            None,
            description="List of extracted data tables from the paper"
        )

        def format(self) -> str:
            # Build a markdown section for the data tables
            if self.data_tables:
                tables_md = ""
                for idx, table in enumerate(self.data_tables, 1):
                    tables_md += f"\n**Table {idx}:**\n"
                    if table.table_caption:
                        tables_md += f"- **Caption:** {table.table_caption}\n"
                    if table.table_summary:
                        tables_md += f"- **Summary:** {table.table_summary}\n"
                    if table.table_content:
                        tables_md += f"- **Content:**\n{table.table_content}\n"
            else:
                tables_md = "No data tables extracted."

            return f"""
**File Search Task:**

{self.file_search_task}

**File Consulted:**

{self.file_consulted}

**Results:**

{self.results}

**Extracted Data Tables:**
{tables_md}
            """
