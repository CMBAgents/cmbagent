import cmbagent




task = r"""
Compute the sum of the first 1000 natural numbers.
"""

results = cmbagent.one_shot(task,
                   max_rounds=20,
                   agent='engineer',
                   # engineer_model='gpt-4.1-2025-04-14',
                #    engineer_model='gemini-2.0-flash',
                   engineer_model="gemini-2.5-pro-preview-03-25",
                            
                   work_dir="/Users/boris/Desktop/one_shot",
                  )
