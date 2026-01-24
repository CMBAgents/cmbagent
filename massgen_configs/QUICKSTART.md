# MassGen Quick Start

Get high-quality code generation with CMBAgent using MassGen's multi-agent system.

## TL;DR

```python
import cmbagent

results = cmbagent.one_shot(
    task="Your coding task here",
    agent="engineer",
    use_massgen=True,  # ← Add this one line!
    work_dir="./output"
)
```

**What happens:**
1. First attempt: Multiple AI agents collaborate (high quality) ✨
2. If code fails: Single AI fixes it quickly (fast debugging) ⚡
3. Best of both worlds!

## How It Works

```
┌─────────────────────────────────┐
│  Attempt 1: MassGen             │
├─────────────────────────────────┤
│  ├─ GPT-5: Proposes solution    │
│  ├─ Gemini: Proposes solution   │
│  ├─ Agents vote on best         │
│  └─ Winner's code selected      │
│     → High-quality initial code │
└─────────────────────────────────┘
             ↓
        ❌ Code fails?
             ↓
┌─────────────────────────────────┐
│  Attempts 2+: Single LLM        │
├─────────────────────────────────┤
│  Single AI debugs the error     │
│     → Fast iteration            │
│     → ✅ Success!               │
└─────────────────────────────────┘
```

## Usage Modes

### Hybrid Mode (Default) ⭐
**Best for most tasks**

```python
cmbagent.one_shot(
    task="...",
    use_massgen=True,  # That's it! Hybrid is the default
)
```

- First attempt: Multi-agent collaboration (quality)
- Retries: Single LLM debugging (speed)
- **Best quality/cost balance**

### Single LLM Only
**Simple tasks**

```python
cmbagent.one_shot(
    task="...",
    use_massgen=False,  # Standard single LLM
)
```

- All attempts: Single AI
- Faster for straightforward tasks

## Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `use_massgen` | `False` | Enable MassGen multi-agent hybrid mode |
| `massgen_config` | `None` | Custom config path (optional) |
| `massgen_verbose` | `False` | Show agent coordination (⚠️ VERY NOISY - not recommended) |
| `massgen_enable_logging` | `True` | Save detailed logs to `.massgen/` |

## Complete Example

```python
import cmbagent

task = """
Implement a function to compute factorial using recursion.
Handle edge cases (0, 1, negative numbers).
Include tests for n=0, n=5, n=10.
Print results with timing information.
"""

results = cmbagent.one_shot(
    task=task,
    max_rounds=50,
    agent="engineer",
    work_dir="/path/to/output",
    clear_work_dir=True,
    # MassGen hybrid mode
    use_massgen=True,
    massgen_verbose=False,  # Clean output
    massgen_enable_logging=True,  # Save logs
)

print(f"Time: {results['execution_time']:.2f}s")
print(f"Output: {results['final_context']['work_dir']}")
```

## Performance Comparison

Based on typical tasks:

| Mode | Quality | Debug Speed | Cost | Best For |
|------|---------|-------------|------|----------|
| Single LLM | ⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | Simple tasks |
| **Hybrid** ⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐ | **Most tasks** |

## What You Get

### With MassGen Hybrid Mode:
✅ Higher quality initial code (multi-agent collaboration)
✅ Automatic code review (agents critique each other)
✅ Best algorithm selection (voting picks optimal approach)
✅ Fast debugging (single LLM for quick fixes)
✅ Cost-efficient (multi-agent only for first attempt)

### Logs Saved To:
`.massgen/massgen_logs/` with:
- Agent responses
- Voting results
- Consensus details
- Token usage

**To view logs after execution:**
```bash
# Find latest log directory
ls -lt .massgen/massgen_logs/ | head -5

# View execution metadata
cat .massgen/massgen_logs/log_YYYYMMDD_HHMMSS_XXXXXX/turn_1/attempt_1/execution_metadata.yaml

# View agent answers
find .massgen/massgen_logs/log_YYYYMMDD_HHMMSS_XXXXXX -name "answer.txt"
```

This is much cleaner than using `massgen_verbose=True`!

## Next Steps

1. **Try it:** Just add `use_massgen=True` to your code
2. **Run tests:**
   - Engineer: `python tests/test_one_shot_engineer_massgen.py`
   - Deep Research: `python tests/test_deep_research_massgen.py`
3. **Learn more:** See `INTEGRATION_GUIDE.md` for advanced usage

## Troubleshooting

**Problem:** "ImportError: No module named 'massgen'"
**Solution:** `pip install massgen`

**Problem:** Output is cluttered with token-by-token logs like `[engineer_openai] only`
**Solution:** Set `massgen_verbose=False` (this is the default). Verbose mode shows every single token, creating thousands of lines of output. Use logs instead: check `.massgen/massgen_logs/`

**Problem:** Want to see what agents are doing
**Solution:** Don't use `massgen_verbose=True` - check the logs instead:
```bash
ls -lt .massgen/massgen_logs/ | head -5  # Find latest run
cat .massgen/massgen_logs/log_*/turn_1/attempt_1/execution_metadata.yaml
```

## Questions?

- **Full docs**: See `INTEGRATION_GUIDE.md`
- **Examples**: `tests/test_one_shot_engineer_massgen.py` and `tests/test_deep_research_massgen.py`
- **Config**: Edit `engineer_massgen.yaml` to customize agents
