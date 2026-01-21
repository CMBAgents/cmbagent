import cmbagent


def test_one_shot():
    task = r"""
    Compute the sum of the first 1000 natural numbers.
    """
    
    results = cmbagent.one_shot(task,
                       max_rounds=20,
                       agent='engineer',
                       engineer_model="gemini-2.5-flash",
                       work_dir="/Users/boris/Desktop/one_shot",
                      )
    
    assert results is not None
