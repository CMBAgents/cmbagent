from .cmbagent import CMBAgent, make_rag_agents
from .version import __version__

print(f"CMBAgent version: {__version__}")


from .data_retriever import set_and_get_rag_data
set_and_get_rag_data()
