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
- Ask idea_maker to generate 5 new research project ideas related to the dataset.
- Ask idea_hater to critique these ideas.
- Ask idea_maker to select and improve 2 out of the 5 research project ideas given the output of the idea_hater.
- Ask idea_hater to critique the 2 improved ideas. 
- Ask idea_maker to select the best idea out of the 2. It should be the only one to be reported. 

"""

cmbagent.solve(task,
                    max_rounds=3,
                    initial_agent="plan_setter",
                    shared_context = {'feedback_left': 1,
                                       'maximum_number_of_steps_in_plan': 5,
                                       'planner_append_instructions': plan_instructions,
                                       'plan_reviewer_append_instructions': plan_instructions}
                    )
        