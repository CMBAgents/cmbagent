import os
import re


os.environ["CMBAGENT_DEBUG"] = "false"
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
How can i run an MCMC with cobaya using lattest Planck 2pt data, both highl and low-l?
"""

cmbagent.solve(task,
               max_rounds=50,
               initial_agent='cobaya_agent',
               mode = "one_shot",
              )