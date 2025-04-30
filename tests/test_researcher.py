import os
import re


os.environ["CMBAGENT_DEBUG"] = "true"
os.environ["CMBAGENT_DISABLE_DISPLAY"] = "true"

from cmbagent import CMBAgent


cmbagent = CMBAgent(agent_llm_configs = {
            'engineer': {
                "model": "gemini-2.5-pro-exp-03-25",
                "api_key": os.getenv("GEMINI_API_KEY"),
                "api_type": "google"
                }
                })


task = r"""
Here is a sentence in english: "I have a Masters in Computer Science and I love NLP". 

Translate it into Italian.

Your final answer should be in format: 

####################\n
Translated sentence:\n
<result>\n
####################\n
"""

cmbagent.solve(task,
               max_rounds=50,
               initial_agent='researcher',
               mode = "one_shot",
              )