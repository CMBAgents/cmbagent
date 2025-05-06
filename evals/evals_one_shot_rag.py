import os
import re


os.environ["CMBAGENT_DEBUG"] = "false"
os.environ["ASTROPILOT_DISABLE_DISPLAY"] = "true"


import cmbagent 
path_to_targets = '/Users/boris/CMBAgents/cmbagent/evals/'


task1 = {"input":r"""
What is the best fit value or H0 measured by Planck 2018 analysis?

Instructions, pick the correct one out of the following options:

A: 67.4
B: 67.65
C: 66.67

You should only report the letter in your answer, in the following format:

<ANSWER>
Correct answer: <A,B, or C>
</ANSWER>

(Dont put the formatting characters <ANSWER> or </ANSWER> in your response.)
""",
         "metadata": {"target_file_path": f"{path_to_targets}/targets/target_answers.csv",
                      "initial_agent": "planck_agent",
                      }
         }


mytasks = [task1]




from openai import AsyncOpenAI
from typing import Any
import os
# from cmbagent import CMBAgent

import cmbagent 
from inspect_ai.solver import solver

from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.scorer import match
from inspect_ai.solver import bridge
from inspect_ai import eval

async def my_agent(task, metadata):

        # cmbagent = CMBAgent()
        # cmbagent = CMBAgent(agent_llm_configs = {
        #                     'engineer': {
        #                         "model": "gemini-2.5-pro-exp-03-25",
        #                         "api_key": os.getenv("GEMINI_API_KEY"),
        #                         "api_type": "google"}})


        # cmbagent.solve(task,
        #                max_rounds=50,
        #                initial_agent=metadata['initial_agent'],
        #                mode = metadata['mode'],
        #                shared_context = metadata['shared_context'] if 'shared_context' in metadata else None
        #                )

        results = cmbagent.one_shot(task,
                                    max_rounds=10,
                                    initial_agent=metadata['initial_agent'],
                                    )


        def get_result(cmbagent_results):
            chat_history = cmbagent_results['chat_history']
            try:
                for obj in chat_history[::-1]:
                    if obj['name'] == 'planck_agent':
                        result = obj['content']
                        break
                task_result = result
            except:
                return None
        
            # Regular expression to find the correct answer letter (A, B, or C)
            match = re.search(r"Correct answer:\s*([A-C])", task_result)
            if match:
                return match.group(1)
            else:
                print("No correct answer letter found.")
                return None


        def get_solution(cmbagent_results,metadata):
        
            cmbagent_answer = get_result(cmbagent_results)
            # target_answer = ... load from task "metadata" etc....
            target_answer = 'B' # to play around, ideally this is loaded from the benchmark evaluation dataset. 
        
            if cmbagent_answer == target_answer:
                return "PASSED"
            else:
                return "FAILED"

        return get_solution(results,metadata)

#### MULTIPLE TASKS WITH AWAIT #####
@solver
def my_solver():
      async def run(tasks: dict[str, Any]) -> dict[str, Any]:
        print(tasks)
        print(tasks['metadata'])
        input = [input for input in tasks['input']]
        result = await my_agent(input, tasks['metadata'])
        return {"output":result}
      return run

@task
def my_task(mytasks):
    return Task(
        dataset=[Sample(input=mytask['input'], 
                        metadata= mytask['metadata'],
                        target="PASSED") for mytask in mytasks],
        solver=bridge(my_solver()),
        scorer=match(location="exact"),
    )



logs = eval(
    my_task(mytasks)
)

