import os
import re


os.environ["CMBAGENT_DEBUG"] = "false"
os.environ["CMBAGENT_DISABLE_DISPLAY"] = "true"


import cmbagent


data_description = r"""
The crucial point for the solution of the question of structural losses is concerned with the importance of every specific case-study. As the structural realist will have to find representative cases of structural continuities, the same holds for those arguing in favor of structural losses. Is the case of the continuity of structure from Fresnel's to Maxwell's theory convincing enough? Surely not.

Given this piece of text, we want to explore interesting philosophical questions. No code or calculations are needed in this project.
"""




cmbagent.planning_and_control(data_description,
                              n_plan_reviews = 1,
                              max_plan_steps = 6,
                            #   engineer_model = "gpt-4o",
                            # researcher_model = "gpt-4o",
                              # researcher_model = "gemini-2.5-pro-exp-03-25",
                              # planner_model = "gemini-2.0-flash",
                            #   planner_model = "gemini-2.5-pro-exp-03-25",
                            #   plan_reviewer_model = "gemini-2.0-flash",
                            #   researcher_model = "gemini-2.0-flash",
                            #   idea_maker_model = "gemini-2.0-flash",
                            idea_maker_model = "gemini-2.5-pro-exp-03-25",
                            #   idea_hater_model = "claude-3-7-sonnet-20250219",
                              plan_instructions=r"""
Given these datasets, and information, make a plan according to the following instructions: 

- Ask idea_maker to generate 5 new research project ideas related to the datasets.
- Ask idea_hater to critique these ideas.
- Ask idea_maker to select and improve 2 out of the 5 research project ideas given the output of the idea_hater.
- Ask idea_hater to critique the 2 improved ideas. 
- Ask idea_maker to select the best idea out of the 2. 
- Ask idea_maker to report the best idea in the form of a scientific paper title with a 1 sentence description. 

The goal of this task is to generate a research project idea based on the data of interest. 
Don't suggest to perform any calculations or analyses here. The only goal of this task is to obtain the best possible project idea.
""")


# results = cmbagent.planning_and_control(data_description,
#                         n_plan_reviews = 1,
#                         max_plan_steps = 6,
#                         idea_maker_model = self.idea_maker_model,
#                         idea_hater_model = self.idea_hater_model,
#                         plan_instructions=self.planner_append_instructions,
#                         work_dir = self.idea_dir
#                         )





