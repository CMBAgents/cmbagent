"""Test one-shot engineer with MassGen multi-agent backend (hybrid mode)."""

import cmbagent


def test_one_shot_engineer_massgen():
    """Test engineer with MassGen hybrid mode.

    Hybrid strategy:
    - First attempt: MassGen multi-agent collaboration (quality)
    - Retries: Single LLM debugging (speed)
    """
    task = "Compute the sum of the first 1000 natural numbers and print the result."

    print("\n" + "="*60)
    print("One-Shot Engineer with MassGen (Hybrid Mode)")
    print("="*60)

    results = cmbagent.one_shot(
        task,
        max_rounds=50,
        agent="engineer",
        work_dir="/Users/boris/Desktop/test_one_shot_engineer_massgen",
        clear_work_dir=True,
        # MassGen hybrid mode
        use_massgen=True,
        massgen_verbose=True,  # Clean output
        massgen_enable_logging=True,  # Logs to .massgen/
    )

    print("\n" + "="*60)
    print("✅ Test completed!")
    print("="*60)
    print(f"Execution time: {results['execution_time']:.2f}s")

    assert results is not None
    assert 'chat_history' in results
    return results


if __name__ == "__main__":
    test_one_shot_engineer_massgen()
