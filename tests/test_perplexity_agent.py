import os
import re


os.environ["CMBAGENT_DEBUG"] = "true"
os.environ["CMBAGENT_DISABLE_DISPLAY"] = "true"

from cmbagent import CMBAgent


cmbagent = CMBAgent()


task = r"""
What is the difference between CAMB and CLASS?

- Instructions:
    - Use perplexity agent to find the answer.

"""

cmbagent.solve(task,
               max_rounds=5,
               initial_agent='perplexity',
               mode = "one_shot",
              )