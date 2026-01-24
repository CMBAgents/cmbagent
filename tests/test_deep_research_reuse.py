from pathlib import Path
import cmbagent


def test_deep_research_reuse():

    task = r"""
In first step write python script where you define a function that computes the sum of the first 1000 natural numbers.
In second step evaluate the function you defined in the first step (import the function and call it), including profiling the execution time and memory usage.
    """

    results = cmbagent.deep_research(
        task,
        max_rounds_control=100,
        n_plan_reviews=0,
        max_n_attempts=2,
        max_plan_steps=2,
        # engineer_model="gemini-3-flash-preview",
        engineer_model="gemini-2.5-flash",
        plan_reviewer_model="claude-sonnet-4-20250514",
        plan_instructions=r"""
use engineer for the whole analysis. Plan must have 2 steps:
1. engineer
2. engineer
""",
        # work_dir=str(tmp_path / "deep_research"),
        work_dir="/Users/boris/Desktop/deep_research",
        clear_work_dir=True,
    )

    assert results is not None


test_deep_research_reuse()