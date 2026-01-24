from pathlib import Path
import cmbagent


def test_deep_research():

    task = r"""
    Compute the sum of the first 1000 natural numbers and then compute the factorial of 10.
    """

    results = cmbagent.deep_research(
        task,
        max_rounds_control=100,
        n_plan_reviews=1,
        max_n_attempts=2,
        max_plan_steps=3,
        # engineer_model="gemini-3-flash-preview",
        engineer_model="gemini-2.5-flash",
        researcher_model="gpt-4.1-2025-04-14",
        plan_reviewer_model="claude-sonnet-4-20250514",
        plan_instructions=r"""
Use researcher to do some reasoning and then use engineer for the whole analysis. Plan must have 3 steps:
1. researcher
2. engineer
3. engineer
""",
        # work_dir=str(tmp_path / "deep_research"),
        work_dir="/Users/boris/Desktop/deep_research",
        clear_work_dir=True,
    )

    assert results is not None


test_deep_research()