from pathlib import Path
import cmbagent



def test_one_shot(tmp_path):

   task = r"""
   Compute the sum of the first 1000 natural numbers.
   """
   
   results = cmbagent.one_shot(
      task,
      max_rounds=20,
      agent="engineer",
      engineer_model="gemini-3-flash-preview",
      work_dir=str(tmp_path / "one_shot"),
   )
   
   assert results is not None
