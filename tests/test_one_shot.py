import os
import re

os.environ["CMBAGENT_DEBUG"] = "false"
os.environ["ASTROPILOT_DISABLE_DISPLAY"] = "true"
import copy
import cmbagent




task = r"""
Compute the sum of the first 1000 natural numbers.
"""

results = cmbagent.one_shot(task,
                   max_rounds=20,
                   initial_agent='engineer',
                   # engineer_model='gpt-4.1-2025-04-14',
                   engineer_model='gemini-2.0-flash',
                            
                   work_dir="/Users/boris/Desktop/one_shot",
                  )
