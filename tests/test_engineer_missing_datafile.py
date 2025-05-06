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

# cmbagent = CMBAgent(agent_llm_configs = {
#             'engineer': {
#                 "model": "o3-mini-2025-01-31",
#                 "reasoning_effort": "medium", # high
#                 "api_key": os.getenv("OPENAI_API_KEY"),
#                 "api_type": "openai"
#                 }
#                 })

cmbagent = CMBAgent(agent_llm_configs = {
            'engineer': {
                "model": "gpt-4o-mini",
                # "reasoning_effort": "medium", # high
                "api_key": os.getenv("OPENAI_API_KEY"),
                "api_type": "openai"
                }
                })
task = r"""
Load data from file `data/data.csv` and plot the data.
"""

cmbagent.solve(task,
               max_rounds=20,
               initial_agent='engineer',
               mode = "one_shot",
              )