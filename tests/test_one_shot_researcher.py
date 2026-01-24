from pathlib import Path
import cmbagent


def test_one_shot_researcher():

    task = r"""
    Provide a 500 words summary on the next opportunities for superhuman scientific research with agentic ai. 
    """
    
    work_dir = "/Users/boris/Desktop/one_shot_researcher"
    
    results = cmbagent.one_shot(task,
                       max_rounds=50,
                       agent='researcher',
                       researcher_model='gemini-2.5-flash',
                       researcher_filename='superhuman_research',
                       work_dir=work_dir,
                       clear_work_dir=True,
                      )
    
    assert results is not None


test_one_shot_researcher()
