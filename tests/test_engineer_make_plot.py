import os
import re


os.environ["CMBAGENT_DEBUG"] = "false"
os.environ["CMBAGENT_DISABLE_DISPLAY"] = "true"

from cmbagent import CMBAgent


# cmbagent = CMBAgent(agent_llm_configs = {
#             'engineer': {
#                 "model": "gemini-2.5-pro-exp-03-25",
#                 "api_key": os.getenv("GEMINI_API_KEY"),
#                 "api_type": "google"
#                 }
#                 })

cmbagent = CMBAgent()
task = r"""
Make a plot y=x2.
"""

cmbagent.solve(task,
               max_rounds=50,
               initial_agent='engineer',
               mode = "one_shot",
              )