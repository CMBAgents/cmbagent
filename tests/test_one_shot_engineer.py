from pathlib import Path
import cmbagent



def test_one_shot_engineer():

   task = r"""
   Compute the sum of the first 1000 natural numbers.
   """


   results = cmbagent.one_shot(
      task,
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