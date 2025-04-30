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
Draw two random numbers from normal distribution, then compute their sum.

Your final answer should be in format: 

####################\n
FINAL RESULT:\n
<result>\n
####################\n
"""

cmbagent.solve(task,
               max_rounds=5,
               initial_agent='engineer',
               mode = "one_shot",
              )