# MassGen Integration Guide for CMBAgent

MassGen is now fully integrated into CMBAgent as an optional backend for the engineer agent. This enables multi-agent code generation where multiple AI agents collaborate through planning, voting, and consensus to produce higher-quality code.

## Quick Start

```python
import cmbagent

results = cmbagent.one_shot(
    task="Your coding task here",
    agent="engineer",
    use_massgen=True,  # Enable MassGen multi-agent backend
    work_dir="./output"
)
```

That's it! Just add `use_massgen=True` to use multi-agent code generation.

**Hybrid Strategy (Default):**
- **First attempt**: MassGen (multi-agent collaboration for quality)
- **Retries/debugging**: Single LLM (fast, cheap iteration)

This gives you the best of both worlds!

## How It Works

### Standard Engineer (Single LLM)
```
User Task → Engineer Agent (GPT-5-nano) → Code → Execution
```

### Hybrid MassGen Engineer (Default Recommended)
```
┌─────────────────────────────────────────────────────────┐
│ INITIAL CODE GENERATION (Attempt 1)                     │
└─────────────────────────────────────────────────────────┘
User Task → MassGen Orchestrator
              ├─ Agent 1 (GPT-5-nano): Plans approach
              ├─ Agent 2 (Gemini-2.5-flash): Plans approach
              ↓
            Voting Phase: Agents critique solutions
              ↓
            Consensus: Best solution selected
              ↓
            High-quality Code → Execution
              ↓
            ❌ Code fails with error
              ↓
┌─────────────────────────────────────────────────────────┐
│ DEBUGGING (Attempts 2+)                                  │
└─────────────────────────────────────────────────────────┘
Error Message → Single Engineer LLM
                  ↓
              Quick Fix → Execution
                  ↓
              ✅ Success!
```

**Key Benefit:** Quality first-pass code + fast debugging iterations

### Always-MassGen Mode (Optional)
```
Every Attempt → MassGen Multi-Agent
              ↓
          Higher quality but slower/more expensive
```

## Parameters

### `use_massgen` (bool, default: False)
Enable MassGen multi-agent backend for the engineer.

```python
cmbagent.one_shot(
    task="...",
    use_massgen=True
)
```

### `massgen_config` (str, optional)
Path to custom MassGen YAML configuration. If `None`, uses default config at `massgen_configs/engineer_massgen.yaml`.

```python
cmbagent.one_shot(
    task="...",
    use_massgen=True,
    massgen_config="/path/to/custom_config.yaml"
)
```

### `massgen_verbose` (bool, default: False)
Enable verbose console output showing MassGen's internal coordination.

**⚠️ Warning:** Verbose mode shows token-by-token streaming which creates very noisy output:
```
[engineer_openai] only
[engineer_openai] when
[engineer_openai] the
[engineer_openai] script
```

**Recommendation:** Keep this `False` and check logs in `.massgen/massgen_logs/` instead.

```python
# NOT RECOMMENDED (very noisy output)
cmbagent.one_shot(
    task="...",
    use_massgen=True,
    massgen_verbose=True  # Token-by-token spam
)

# RECOMMENDED (clean output, detailed logs saved)
cmbagent.one_shot(
    task="...",
    use_massgen=True,
    massgen_verbose=False,  # Clean console
    massgen_enable_logging=True  # Detailed logs saved to .massgen/
)
```

### `massgen_enable_logging` (bool, default: True)
Save detailed logs to `.massgen/massgen_logs/`.

```python
cmbagent.one_shot(
    task="...",
    use_massgen=True,
    massgen_enable_logging=True  # Save logs for later review
)
```

### `massgen_use_for_retries` (bool, default: False)
Control whether MassGen is used for retry attempts after code failures.

**Default (False) - Hybrid Mode:**
- First attempt: MassGen multi-agent (quality)
- Retries: Single LLM (fast debugging)

This is the recommended setting and you don't need to specify it explicitly:

```python
# Hybrid mode (default, recommended)
cmbagent.one_shot(
    task="...",
    use_massgen=True  # massgen_use_for_retries=False is the default
)
```

Note: You can set `massgen_use_for_retries=True` to use MassGen for all attempts, but this is slower and more expensive.

## Complete Example

```python
import cmbagent

task = """
Implement a function to compute the n-th Fibonacci number using dynamic programming.
Include tests for n=10, n=20, and n=30.
Print execution time for each test.
"""

# Hybrid MassGen: Quality + Efficiency
results = cmbagent.one_shot(
    task=task,
    max_rounds=20,
    max_n_attempts=3,  # Allow retries if needed
    agent="engineer",
    work_dir="/path/to/output",
    clear_work_dir=True,

    # MassGen parameters (hybrid mode)
    use_massgen=True,  # Enable MassGen
    massgen_config=None,  # Use default config
    massgen_verbose=False,  # Clean console output
    massgen_enable_logging=True,  # Save detailed logs
    massgen_use_for_retries=False,  # Use single LLM for retries (RECOMMENDED)
)

print(f"Execution time: {results['execution_time']:.2f}s")
print(f"Check output: {results['final_context']['work_dir']}")

# What happens:
# 1. First attempt: MassGen multi-agent collaboration (high quality)
# 2. If code fails: Single LLM debugging (fast iteration)
# 3. Best of both worlds!
```

## Benefits

### Higher Code Quality
Multiple agents propose different solutions. The best approach is selected through voting, resulting in more robust code.

### Error Resilience
Different models (GPT, Gemini) bring complementary strengths. If one model struggles with a particular pattern, another may excel.

### Automatic Code Review
Agents critique each other's solutions during the voting phase, catching potential issues early.

### Diverse Approaches
Different models may use different algorithms or optimizations. The voting process selects the most efficient approach.

## Performance Comparison

| Metric | Single LLM | Hybrid MassGen (Recommended) | Always MassGen |
|--------|-----------|------------------------------|----------------|
| **Initial Code Quality** | Good | Excellent ⭐ | Excellent |
| **Debugging Speed** | Fast | Fast ⭐ | Slower |
| **Error Handling** | Basic | Robust ⭐ | Very Robust |
| **Algorithm Efficiency** | Depends on model | Best of multiple ⭐ | Best of multiple |
| **First Attempt Latency** | ~5-10s | ~15-30s | ~15-30s |
| **Retry Latency** | ~5-10s | ~5-10s ⭐ | ~15-30s |
| **Cost (per task)** | 1x | ~1.2-1.5x ⭐ | ~2-3x |

**⭐ Hybrid MassGen wins:** Best quality-to-cost ratio
- High-quality initial code (multi-agent)
- Fast debugging iterations (single LLM)
- Optimal cost/performance balance

## When to Use Each Mode

### ⭐ Hybrid MassGen (Recommended for Most Tasks)
**Use when:**
- You want quality code without sacrificing debugging speed
- Task is moderately complex
- You care about both quality and cost-efficiency
- Standard workflow for production code

**Benefits:**
- Quality multi-agent initial code
- Fast single-LLM debugging
- Best cost/performance ratio

```python
use_massgen=True,
massgen_use_for_retries=False  # Default
```

### 🔧 Always-MassGen Mode
**Use when:**
- Debugging itself is complex and benefits from multi-agent
- Maximum code quality is critical at any cost
- Complex algorithmic challenges throughout
- Research or highly experimental code

```python
use_massgen=True,
massgen_use_for_retries=True
```

### 🚀 Single LLM Only
**Use when:**
- Simple, straightforward tasks
- Rapid prototyping where speed matters most
- Very tight budget constraints
- Task has one obvious solution

```python
use_massgen=False
```

## Example Comparison

```python
import cmbagent

task = "Implement merge sort algorithm with timing analysis"

# Test 1: Single LLM
results_single = cmbagent.one_shot(
    task=task,
    agent="engineer",
    engineer_model="gpt-5-nano",
    use_massgen=False,
    work_dir="./single_llm_output"
)

# Test 2: Hybrid MassGen (Recommended)
results_hybrid = cmbagent.one_shot(
    task=task,
    agent="engineer",
    use_massgen=True,  # Hybrid mode is default
    work_dir="./hybrid_massgen_output"
)

# Compare outputs
print(f"Single LLM:      {results_single['execution_time']:.2f}s")
print(f"Hybrid MassGen:  {results_hybrid['execution_time']:.2f}s  ⭐")
```

**Typical results:**
- Single LLM: Fast but may need retries
- Hybrid MassGen: Best quality/cost balance ⭐

Run the tests to see hybrid behavior in action:
- Engineer: `python tests/test_one_shot_engineer_massgen.py`
- Deep Research: `python tests/test_deep_research_massgen.py`

## Customization

### Adding More Agents

Edit `massgen_configs/engineer_massgen.yaml` to add more agents:

```yaml
agents:
  - id: "engineer_openai"
    backend:
      type: "openai"
      model: "gpt-5-nano"
    system_message: |
      [Engineer instructions...]

  - id: "engineer_gemini"
    backend:
      type: "gemini"
      model: "gemini-2.5-flash"
    system_message: |
      [Engineer instructions...]

  - id: "engineer_claude"  # Add Claude
    backend:
      type: "claude"
      model: "claude-sonnet-4-5-20250929"
    system_message: |
      [Engineer instructions...]
```

### Adjusting Voting Sensitivity

```yaml
orchestrator:
  voting_sensitivity: "lenient"  # or "strict", "balanced"
  answer_novelty_requirement: "balanced"  # or "high", "low"
```

## Logs and Debugging

MassGen saves detailed logs to `.massgen/massgen_logs/`:
- Agent responses
- Voting results
- Consensus details
- Token usage stats

Check these logs to understand how agents collaborated and why a particular solution was selected.

```bash
# View latest MassGen logs
ls -lt .massgen/massgen_logs/ | head -n 5

# Read specific log
cat .massgen/massgen_logs/log_YYYYMMDD_HHMMSS_XXXXXX/final/*.txt
```

## Requirements

MassGen must be installed separately:

```bash
pip install massgen
```

If MassGen is not installed and you try `use_massgen=True`, you'll get a helpful error:

```
ImportError: MassGen is required for engineer_backend='massgen'.
Install it with: pip install massgen
```

## Troubleshooting

### Problem: MassGen Not Found
**Solution:** Install MassGen: `pip install massgen`

### Problem: Custom Config Not Loading
**Solution:** Check the path is absolute or relative to your working directory

### Problem: Agents Not Collaborating
**Solution:** Enable `massgen_verbose=True` to see coordination details

### Problem: Logs Not Saving
**Solution:** Check `.massgen/massgen_logs/` exists and is writable

## Summary: Choosing the Right Mode

| Mode | First Attempt | Retries | Best For | Cost |
|------|---------------|---------|----------|------|
| **Single LLM** | Single LLM | Single LLM | Simple tasks, prototyping | 1x |
| **Hybrid** ⭐ | MassGen | Single LLM | Most production tasks | 1.2-1.5x |
| **Always-MassGen** | MassGen | MassGen | Complex research code | 2-3x |

**Our Recommendation:** Start with Hybrid mode (`use_massgen=True, massgen_use_for_retries=False`)

## See Also

- **Engineer Test**: `tests/test_one_shot_engineer_massgen.py` - Hybrid engineer test
- **Deep Research Test**: `tests/test_deep_research_massgen.py` - Hybrid deep research test
- **MassGen Documentation**: https://massgen.readthedocs.io/
- **Default Config**: `massgen_configs/engineer_massgen.yaml`
- **Config README**: `massgen_configs/README.md`
