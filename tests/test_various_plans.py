import os
import re


os.environ["CMBAGENT_DEBUG"] = "false"
os.environ["CMBAGENT_DISABLE_DISPLAY"] = "true"

from cmbagent import CMBAgent


cmbagent = CMBAgent()


task = r"""
We want to generate synthetic data for exploring acoustic oscillations in the CMB.
"""

plan_instructions = r"""
Use classy_sz then engineer and researcher. 
"""

cmbagent.solve(task,
                    max_rounds=3,
                    initial_agent="plan_setter",
                    shared_context = {'feedback_left': 1,
                                       'maximum_number_of_steps_in_plan': 5,
                                       'planner_append_instructions': plan_instructions,
                                       'plan_reviewer_append_instructions': plan_instructions}
                    )
        