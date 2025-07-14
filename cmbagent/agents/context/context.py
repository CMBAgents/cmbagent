import os
from cmbagent.base_agent import BaseAgent

class ContextAgent(BaseAgent):
    def __init__(self, llm_config=None, **kwargs):
        agent_id = os.path.splitext(os.path.abspath(__file__))[0]
        super().__init__(llm_config=llm_config, agent_id=agent_id, **kwargs)

    def set_agent(self, **kwargs):
        super().set_assistant_agent(**kwargs)

    """
    def run(self, library_name, output_path=None, input_path=None):

    #Generate documentation for a library using contextmaker and return the content of the generated txt file.
    
    try:
        doc_path = contextmaker.convert(
            library_name,
            output_path=output_path,
            input_path=input_path
        )
        with open(doc_path, "r") as f:
            documentation = f.read()
        return documentation
    except Exception as e:
        return f"Error generating documentation: {e}"
    """