import os
import re

os.environ["CMBAGENT_DEBUG"] = "false"
os.environ["ASTROPILOT_DISABLE_DISPLAY"] = "true"

import cmbagent



task = r"""
What is the capital of France?
"""

results = cmbagent.one_shot(task,
                   max_rounds=50,
                   initial_agent='engineer',
                   engineer_model='gpt-4o-mini',
                  )