import os
os.environ["CMBAGENT_DEBUG"] = "false"
os.environ["CMBAGENT_DISABLE_DISPLAY"] = "true"
from cmbagent import CMBAgent
cmbagent = CMBAgent()


task1 = r"""
Here is a sentence in english: "I have a Masters in Computer Science and I love NLP". 

Write code that translate it into Italian with the googletrans library and prints it out. 

Your final answer should be in format: 

####################\n
Translated sentence:\n
<result>\n
####################\n
"""


task2 = r"""
Here is a sentence in english: "I love New York". 

Write code that translate it into Italian with the googletrans library and prints it out. 

Your final answer should be in format: 

####################\n
Translated sentence:\n
<result>\n
####################\n
"""

mytasks = [task1, task2]


from openai import AsyncOpenAI
from typing import Any
import os
from cmbagent import CMBAgent
from inspect_ai.solver import solver

from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.scorer import includes
from inspect_ai.solver import bridge
from inspect_ai import eval

async def my_agent(task):
        cmbagent = CMBAgent()


        cmbagent.solve(task,
                       max_rounds=500,
                       initial_agent='engineer',
                       shared_context = {'feedback_left': 0,
                                         "number_of_steps_in_plan": 1,
                                         'maximum_number_of_steps_in_plan': 1})
        # return cmbagent.output
        return "completed"

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
        dataset=[Sample(input=mytask, target="completed") for mytask in mytasks],
        solver=bridge(my_solver()),
        scorer=includes(),
    )

    


logs = eval(
    my_task(mytasks)
)