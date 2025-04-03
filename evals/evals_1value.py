import os
import re
os.environ["CMBAGENT_DEBUG"] = "false"
os.environ["CMBAGENT_DISABLE_DISPLAY"] = "true"
from cmbagent import CMBAgent
cmbagent = CMBAgent()


task1 = r"""
What is the value of H0 corresponding to the following set of cosmological parameter values in LCDM: 

omega_cdm: 0.125
omega_b: 0.0224
ln(10^10 A_s) : 3.05321
n_s: 0.96
100*theta_star: 1.0411


Instructions: 
 - Your answer should be given in the format "%.4f" (in units of km/s/Mpc) and must consist of the number only (don't write H0= xxx km/s/Mpc, just print xxx) 
 - Use camb_agent and engineer.
"""

mytasks = [task1]


from openai import AsyncOpenAI
from typing import Any
import os
from cmbagent import CMBAgent
from inspect_ai.solver import solver

from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.scorer import match
from inspect_ai.solver import bridge
from inspect_ai import eval

async def my_agent(task):
        cmbagent = CMBAgent()


        cmbagent.solve(task,
                       max_rounds=50,
                       initial_agent='planner',
                       shared_context = {'feedback_left': 0,
                                         'maximum_number_of_steps_in_plan': 2})
        
        def get_solution(output):
            match = re.search(r'Execution output:\s*([0-9.]+)', output)
            
            if match:
                number = str(float(match.group(1)))
                print(number)  # (target) 65.6595
            else:
                number = "None"
            return number

        return get_solution(cmbagent.output)

#### MULTIPLE TASKS WITH AWAIT #####
@solver
def my_solver():
      async def run(tasks: dict[str, Any]) -> dict[str, Any]:
        input = [input for input in tasks['input']]
        result = await my_agent(input)
        return {"output":result}
      return run

@task
def my_task(mytasks):
    return Task(
        dataset=[Sample(input=mytask, target="65.6595") for mytask in mytasks],
        solver=bridge(my_solver()),
        scorer=match(location="exact"),
    )



logs = eval(
    my_task(mytasks)
)