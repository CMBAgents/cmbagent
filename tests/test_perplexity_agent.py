import os
import re


os.environ["CMBAGENT_DEBUG"] = "false"
os.environ["CMBAGENT_DISABLE_DISPLAY"] = "true"

from cmbagent import CMBAgent


cmbagent = CMBAgent()


task = r"""
What is the difference between CAMB and CLASS?

- Instructions:
    - Use the perplexity agent to find the answer.

- Output:
    - A list of the differences between CAMB and CLASS

    - References:
       - Title, Author, Year, URL
       - Title, Author, Year, URL
       - Title, Author, Year, URL
       - Title, Author, Year, URL

"""

cmbagent.solve(task,
               max_rounds=10,
               initial_agent='control',
               mode = "one_shot",
              )