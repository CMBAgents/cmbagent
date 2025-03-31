import os
import re


os.environ["CMBAGENT_DEBUG"] = "false"
os.environ["CMBAGENT_DISABLE_DISPLAY"] = "true"

from cmbagent import CMBAgent


cmbagent = CMBAgent()


task = r"""
Compute temperature cls for l=np.arange(2,2000) and the following cosmological
parameter values assuming flat LCDM cosmology:

omega_cdm: 0.125
omega_b: 0.0224
ln(10^10 A_s) : 3.05321
n_s: 0.96
100*theta_star: 1.0411


Instructions: 
 - use camb_agent and engineer
 - Save results in a csv file named result.csv with columns "l" and "cl"
 - cls should be in uK^2
"""

cmbagent.solve(task,
               max_rounds=50,
               initial_agent='camb_agent',
               mode = "one_shot",
              )