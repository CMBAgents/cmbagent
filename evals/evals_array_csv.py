import os
import re
os.environ["CMBAGENT_DEBUG"] = "false"
os.environ["CMBAGENT_DISABLE_DISPLAY"] = "true"
from cmbagent import CMBAgent
cmbagent = CMBAgent()



task1 = r"""

Compute sin(x) for x = 1, 2, 3, 4, 5.

Instructions: 
 - Return a Python-style list of floats with 4 decimal places of precision.
 - Example: [1.1234, 1.1234, 1.1234, 1.1234, 1.1234]

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

        cmbagent = CMBAgent(agent_llm_configs = {
                            'engineer': {
                                "model": "gemini-2.0-pro-exp-02-05",
                                "api_key": os.getenv("GEMINI_API_KEY"),
                                "api_type": "google"}})


        cmbagent.solve(task,
                       max_rounds=50,
                       initial_agent='engineer',
                       mode = "one_shot")
        
        def get_solution(output):
            match = re.search(r'Execution output:\s*([0-9.]+)', output)
            
            if match:
                number = str(float(match.group(1)))
                print(number)  
            else:
                number = "None"
                print(number)
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
        dataset=[Sample(input=mytask, target="[0.8415, 0.9093, 0.1411, -0.7568, -0.9589]") for mytask in mytasks],
        solver=bridge(my_solver()),
        scorer=match(location="exact"),
    )



logs = eval(
    my_task(mytasks)
)