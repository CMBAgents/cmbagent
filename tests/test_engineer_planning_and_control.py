import os
import re


os.environ["CMBAGENT_DEBUG"] = "false"
os.environ["CMBAGENT_DISABLE_DISPLAY"] = "true"

# from cmbagent import CMBAgent

import cmbagent

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

# cmbagent = CMBAgent(agent_llm_configs = {
#             'engineer': {
#                 "model": "gpt-4o-mini",
#                 # "reasoning_effort": "medium", # high
#                 "api_key": os.getenv("OPENAI_API_KEY"),
#                 "api_type": "openai"
#                 },
#             'researcher': {
#                 "model": "gpt-4o-mini",
#                 "api_key": os.getenv("OPENAI_API_KEY"),
#                 "api_type": "openai"
#                 }
#                 })

# cmbagent = CMBAgent()
# Compute scaterring coefficient of the data in `data/data.csv` with kymatio package.
task = r"""

Compute scaterring coefficient of the data in `data/data.csv` with kymatio package.
Create data/data.csv if it does not exist.
"""

# cmbagent.solve(task,
#                max_rounds=20,
#                initial_agent='engineer',
#                mode = "one_shot",
#               )

cmbagent.planning_and_control(task,
                              n_plan_reviews = 0,
                              engineer_model = "gpt-4o",
                              researcher_model = "gpt-4o-mini",
                              plan_instructions=r"""
plan should be in three steps in the following order: 
1. researcher 
2. engineer
3. engineer
""")