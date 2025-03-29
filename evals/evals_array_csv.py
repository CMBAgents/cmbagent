import os
import re


os.environ["CMBAGENT_DEBUG"] = "false"
os.environ["CMBAGENT_DISABLE_DISPLAY"] = "true"



from cmbagent import CMBAgent
cmbagent = CMBAgent()



task1 = {"input":r"""
Compute sin(x) for x = np.linspace(-1,1,100).

Instructions: 
 - Save results in a csv file named result.csv with columns "x" and "sin(x)"
""",
         "metadata": {"target_file_path": f"targets/target_x_sinx.csv",
                      "initial_agent": "engineer",
                      "mode": "one_shot"}
         }

task2 = {"input":r"""
Compute cos(x) for x = np.linspace(-1,1,100).

Instructions: 
 - Save results in a csv file named result.csv with columns "x" and "cos(x)"
""",
         "metadata": {"target_file_path": f"targets/target_x_sinx.csv",
                      "initial_agent": "engineer",
                      "mode": "one_shot"}
         }


task3 = {"input":r"""
Compute sin(x) for x = np.linspace(-10,10,100).

Instructions: 
 - Save results in a csv file named result.csv with columns "x" and "sin(x)"
""",
         "metadata": {"target_file_path": f"targets/target_x_sinx.csv",
                      "initial_agent": "engineer",
                      "mode": "one_shot"}
         }

task4 = {"input":r"""
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
""",
         "metadata": {"target_file_path": f"targets/target_x_sinx.csv",
                      "initial_agent": "planner",
                      "mode": "default",
                      "shared_context": {'feedback_left': 0,
                                          'maximum_number_of_steps_in_plan': 2}
                      }
}



mytasks = [task1, task2, task3, task4]


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

async def my_agent(task, metadata):

        cmbagent = CMBAgent(agent_llm_configs = {
                            'engineer': {
                                "model": "gemini-2.0-pro-exp-02-05",
                                "api_key": os.getenv("GEMINI_API_KEY"),
                                "api_type": "google"}})


        cmbagent.solve(task,
                       max_rounds=50,
                       initial_agent=metadata['initial_agent'],
                       mode = metadata['mode'],
                       shared_context = metadata['shared_context'] if 'shared_context' in metadata else None
                       )
        
        def get_solution():
            import numpy as np
            import pandas as pd
            
            # Load target and output dataframes
            target_file = metadata['target_file_path']
            df_target = pd.read_csv(target_file)
            
            database_full_path = os.path.join(
                cmbagent.work_dir,
                cmbagent.final_context['database_path'],
                "result.csv"
            )
            resolved_path = os.path.abspath(database_full_path)  # resolves the '..'
            df_output = pd.read_csv(resolved_path)
            
            print("Agent Output:")
            print(df_output)
            print("Target Data:")
            print(df_target)
            
            target_columns = list(df_target.columns)
            output_columns = list(df_output.columns)
            
            # Check if columns perfectly match (including order)
            if target_columns != output_columns:
                missing = [col for col in target_columns if col not in output_columns]
                extra = [col for col in output_columns if col not in target_columns]
                
                error_parts = []
                if missing:
                    error_parts.append(f"Missing columns: {missing}")
                if extra:
                    error_parts.append(f"Extra columns: {extra}")
                error_message = "Column mismatch: " + "; ".join(error_parts)
                return error_message
            
            # Check that numerical values in each column match within 1% tolerance
            try:
                for col in target_columns:
                    target_values = df_target[col].to_numpy()
                    output_values = df_output[col].to_numpy()
                    
                    # For numeric columns, assert that values are within 1% of each other
                    if pd.api.types.is_numeric_dtype(df_target[col]):
                        assert np.allclose(target_values, output_values, rtol=0.01, atol=0), \
                            f"Values in column '{col}' do not match within 1% tolerance."
                    else:
                        # For non-numeric columns, require exact match
                        assert (target_values == output_values).all(), \
                            f"Values in column '{col}' do not match exactly."
            except AssertionError as e:
                return str(e)
            
            return "PASSED"

        return get_solution()

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

