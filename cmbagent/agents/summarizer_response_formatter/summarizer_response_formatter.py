import os
from cmbagent.base_agent import BaseAgent
from pydantic import BaseModel, Field
from typing import Optional, List

class SummarizerResponseFormatterAgent(BaseAgent):
    
    def __init__(self, llm_config=None, **kwargs):

        agent_id = os.path.splitext(os.path.abspath(__file__))[0]

        llm_config['config_list'][0]['response_format'] = self.SummarizerResponse

        super().__init__(llm_config=llm_config, agent_id=agent_id, **kwargs)


    def set_agent(self,**kwargs):

        super().set_assistant_agent(**kwargs)


    class SummarizerResponse(BaseModel):
        title: str = Field(
            ...,
            description="The exact title of the document"
        )
        authors: List[str] = Field(
            ...,
            description="A list of authors of the document, if more than 5, only list the first 5 and add et al."
        )
        date: str = Field(
            ...,
            description="The date of the document"
        )
        abstract: str = Field(
            ...,
            description="A 3 sentence summary of the abstract"
        )
        keywords: List[str] = Field(
            ...,
            description="A list of 5 keywords that are relevant to the document"
        )
        key_findings: List[str] = Field(
            ...,
            description="A list of key findings of the document"
        )
        scientific_software: List[str] = Field(
            ...,
            description="List of software explicitly mentioned in the document (provide github links if available)"
        )
        data_sources: List[str] = Field(
            ...,
            description="List of data sources explicitly mentioned in the document (provide links if available)"
        )
        data_sets: List[str] = Field(
            ...,
            description="List of data sets explicitly mentioned in the document (provide links if available)"
        )
        data_analysis_methods: List[str] = Field(
            ...,
            description="List of data analysis methods explicitly mentioned in the document"
        )

        def format(self) -> str:
            # Format the list of authors
            authors_text = ", ".join(self.authors)
            if len(self.authors) > 5:
                authors_text = ", ".join(self.authors[:5]) + " et al."
            
            # Format keywords as comma-separated list
            keywords_text = ", ".join(self.keywords)
            
            # Format lists as bullet points
            key_findings_text = "\n".join(f"- {finding}" for finding in self.key_findings)
            scientific_software_text = "\n".join(f"- {software}" for software in self.scientific_software)
            data_sources_text = "\n".join(f"- {source}" for source in self.data_sources)
            data_sets_text = "\n".join(f"- {dataset}" for dataset in self.data_sets)
            data_analysis_methods_text = "\n".join(f"- {method}" for method in self.data_analysis_methods)

            # Note: JSON saving is handled by the main summarizer function
            
            return (
                f"# {self.title}\n\n"
                f"**Authors:** {authors_text}\n\n"
                f"**Date:** {self.date}\n\n"
                f"**Abstract:** {self.abstract}\n\n"
                f"**Keywords:** {keywords_text}\n\n"
                f"**Key Findings:**\n{key_findings_text}\n\n"
                f"**Scientific Software:**\n{scientific_software_text}\n\n"
                f"**Data Sources:**\n{data_sources_text}\n\n"
                f"**Data Sets:**\n{data_sets_text}\n\n"
                f"**Data Analysis Methods:**\n{data_analysis_methods_text}"
            )





