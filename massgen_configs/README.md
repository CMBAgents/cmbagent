# MassGen Integration for CMBAgent

This directory contains MassGen configurations for integrating multi-agent code generation with CMBAgent's engineer workflow.

## Overview

MassGen (Multi-Agent System Generator) is a framework that coordinates multiple AI agents to solve complex tasks through:
- **Parallel processing**: Multiple agents work simultaneously
- **Intelligence sharing**: Agents observe each other's progress
- **Consensus building**: Voting to select the best solution

## Hybrid Strategy (Default & Recommended)

**MassGen now uses a hybrid approach:**
- ✅ **First code generation**: MassGen multi-agent (high quality)
- ✅ **Debugging/retries**: Single LLM (fast iteration)

This provides the best balance of quality, speed, and cost!

## Configuration Files

### `engineer_massgen.yaml`

Multi-agent configuration for the engineer agent's code generation tasks.

**Agents:**
- `engineer_openai`: GPT-5-nano backend
- `engineer_gemini`: Gemini-2.5-flash backend

**System Message:**
The configuration includes the engineer's complete formatting requirements and coding rules from `cmbagent/agents/coding/engineer/engineer.yaml`:
- Structured response format (Code Explanation, Modifications, Python Code)
- No f-strings or .format() - use string concatenation
- No comments in code - use docstrings
- High-resolution plots (dpi>=300) saved to files
- No LaTeX rendering in plots
- Suppress verbose training output
- Performance optimizations (vectorization, loop optimization)

## Usage

### Basic Test

```python
from pathlib import Path
import asyncio
import massgen

# Path to config
MASSGEN_CONFIG = "massgen_configs/engineer_massgen.yaml"

# Simple task
async def test_massgen():
    result = await massgen.run(
        query="Compute the sum of first 1000 natural numbers",
        config=MASSGEN_CONFIG,
        verbose=False,
        enable_logging=True
    )

    print(f"Selected agent: {result['selected_agent']}")
    print(f"Final answer:\n{result['final_answer']}")
    print(f"Logs: {result['log_directory']}")

asyncio.run(test_massgen())
```

### Full Integration Test

See `tests/test_one_shot_massgen.py` for complete examples including:
- Formatting the engineer prompt with full context variables
- Error recovery loops with retry logic
- Code extraction and execution
- Integration with CMBAgent's execution workflow

## Prompt Formatting

The engineer agent uses a detailed prompt template with context variables. When integrating MassGen:

1. **System Message** (in YAML config): General engineering rules and formatting requirements
2. **Query** (passed to `massgen.run()`): Task-specific context including:
   - `improved_main_task`: The main task description
   - `final_plan`: Execution plan
   - `current_sub_task`: Current subtask
   - `database_path`: Path for saving outputs
   - `previous_steps_execution_summary`: Context from prior steps

Example from `test_one_shot_massgen.py`:

```python
from cmbagent.utils.yaml import yaml_load_file

def format_engineer_prompt(task: str, context: dict = None) -> str:
    """Format engineer prompt with full context."""
    engineer_info = yaml_load_file("cmbagent/agents/coding/engineer/engineer.yaml")
    template = engineer_info['instructions']

    context = context or {}
    context.setdefault('improved_main_task', task)
    context.setdefault('final_plan', 'No formal plan - one-shot execution')
    context.setdefault('database_path', './data')
    # ... more defaults

    return template.format(**context)
```

## Running Tests

```bash
cd cmbagent
python tests/test_one_shot_massgen.py
```

This will run two tests:
1. **Basic code generation**: MassGen generates code for a simple task
2. **Full integration with error recovery**: Multi-attempt execution with error feedback

## Output

MassGen logs are saved to `.massgen/massgen_logs/` with:
- Agent responses
- Voting results
- Consensus details
- Token usage statistics

## Benefits of Multi-Agent Code Generation

1. **Higher quality code**: Multiple agents propose solutions, best one is selected through voting
2. **Error resilience**: Different approaches increase chance of finding correct solution
3. **Diverse perspectives**: Different models (GPT, Gemini) bring complementary strengths
4. **Automatic code review**: Agents critique each other's solutions during voting phase

## Configuration Customization

You can modify `engineer_massgen.yaml` to:
- Add more agents (e.g., Claude, Grok)
- Change voting sensitivity
- Adjust timeout settings
- Modify system messages for specialized domains

Example: Add Claude agent:

```yaml
agents:
  - id: "engineer_claude"
    backend:
      type: "claude"
      model: "claude-sonnet-4-5-20250929"
      enable_web_search: false
    system_message: |
      You are the engineer agent...
      [same formatting requirements]
```

## Comparison: Single LLM vs MassGen

### Single LLM (standard cmbagent)
```python
results = cmbagent.one_shot(
    task="Compute sum of first 1000 numbers",
    engineer_model="gpt-5-nano",  # Single model
)
```

### MassGen (multi-agent)
```python
# MassGen coordinates 2+ models internally
result = await massgen.run(
    query=format_engineer_prompt(task),
    config="massgen_configs/engineer_massgen.yaml",
)
```

The multi-agent approach typically produces more robust, well-tested code through collaborative intelligence.
