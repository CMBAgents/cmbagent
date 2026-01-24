from pathlib import Path
import cmbagent



def test_one_shot_engineer():

   task = r"""
   Compute the sum of the first 1000 natural numbers.
   """
   task_reused = r"""
execute the following code:
import time
from memory_profiler import profile
from step_1 import sum_first_1000_natural_numbers

@profile
def calculate_and_profile_sum():
   result = sum_first_1000_natural_numbers()
   return result

if __name__ == "__main__":
   start_time = time.perf_counter()
   sum_result = calculate_and_profile_sum()
   end_time = time.perf_counter()

   execution_time = end_time - start_time

   print("--- Profiling Results ---")
   print("Computed sum: " + str(sum_result))
   print("Execution time: " + str(execution_time) + " seconds")
   print("Memory usage details for 'calculate_and_profile_sum' are printed above by memory_profiler.")
   """

   results = cmbagent.one_shot(
      task_reused,
      max_rounds=20,
      agent="engineer",
      # engineer_model="gpt-4.1",
      engineer_model="gemini-2.5-flash",  # Now fixed with shared state
      # work_dir=str(tmp_path / "one_shot"), 
      work_dir="/Users/boris/Desktop/one_shot_engineer",
      clear_work_dir=True,
   )
   
   assert results is not None



test_one_shot_engineer()