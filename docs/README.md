## Installation

To install cmbagent, follow these steps:

### Core Installation (Recommended)

The base installation is lightweight - it includes only orchestration dependencies (AG2, FastAPI, LLM clients). Scientific packages are **not required** because code execution happens on the frontend via remote executor.

```bash
pip install cmbagent
```

Or clone the repository and install it locally (recommended for development):

```bash
git clone https://github.com/CMBAgents/cmbagent.git
cd cmbagent
pip install -e .
```

Creating a virtual environment is recommended:
```bash
python -m venv /path/to/your/envs/cmbagent_env
source /path/to/your/envs/cmbagent_env/bin/activate
```

### Optional Dependencies

**Development tools:**
```bash
pip install cmbagent[dev]  # pytest, ipython, jupyter
```

**Local execution** (if not using the web UI with frontend execution):
```bash
pip install cmbagent[local]  # numpy, scipy, matplotlib, scikit-learn
```

**Domain-specific packages** (for local execution or specialized agents):
```bash
# Material sciences
pip install cmbagent[materials]

# Biochemistry
pip install cmbagent[biochem]

# Astronomy/astrophysics
pip install cmbagent[astro]

# Data science
pip install cmbagent[data]

# Everything
pip install cmbagent[all]

# Or combine multiple
pip install cmbagent[materials,biochem,data]
```

### Architecture Note

When using the **web UI** (`cmbagent-ui`), code executes on your local machine in an isolated virtual environment managed by the frontend. The backend server only handles orchestration - no heavy scientific packages needed there. Packages like numpy, matplotlib, etc. are installed on-demand by the installer agent into the frontend's venv.


## Structure

The core system is located in [cmbagent.py](https://github.com/CMBAgents/cmbagent/blob/main/cmbagent/cmbagent.py).

### Agent Organization

Agents are organized in specialized directories under `cmbagent/agents/`:

- **planning/**: Planner, plan reviewer, plan recorder, plan setter - design and manage task execution strategies
- **control/**: Controller, control starter, terminator - orchestrate workflow execution and manage agent handoffs
- **coding/**: Engineer, executor, executor_bash - write code, execute Python/bash commands, format code outputs
- **research/**: Researcher, summarizer - conduct research analysis and document summarization
- **hypothesis/**: Idea maker, idea hater, idea saver - generate and evaluate research ideas
- **keywords/**: AAAI and AAS keyword finders - extract keywords from scientific literature
- **installer/**: Package installer agent - handle dependency installation
- **admin/**: Human-in-the-loop interface agent for user interaction
- **specialized/**: Domain-specific agents provided as templates - users can add their own

Each agent typically consists of:
- A Python implementation (`.py` file)
- A YAML configuration file (`.yaml` file) with instructions and settings
- Optional response formatters for structured output handling

## Agents

All agents inherit from the `BaseAgent` class. You can find the definition of `BaseAgent` in the [base_agent.py](https://github.com/CMBAgents/cmbagent/blob/main/cmbagent/base_agent.py) file.


## Usage


Before you can use cmbagent, you need to set your OpenAI API key as an environment variable:

For Unix-based systems (Linux, macOS):
```bash
export OPENAI_API_KEY="sk-..."
```
(paste in your bashrc or zshrc file, if possible.)

For Windows:
```cmd
setx OPENAI_API_KEY "sk-..."
```

You can also pass your API key to cmbagent as an argument when you instantiate it:

```python
cmbagent = CMBAgent(llm_api_key="sk-...")
```

Instantiate the CMBAgent with:

```python
from cmbagent import CMBAgent
cmbagent = CMBAgent(verbose=True)
```

Define a task as:

```python
task = """
       Write a Python script that generates synthetic data following a normal distribution,
       fits a linear regression model, and creates a visualization of the results.
       """
```

Or use the one-shot API directly:

```python
import cmbagent

results = cmbagent.one_shot(
    task="Draw two random numbers and give me their sum",
    agent='engineer',
    engineer_model='gpt-4o-mini'
)
```

The system supports multiple execution modes:
- **one_shot**: Direct task execution with a single agent
- **planning_and_control**: Full planning and execution workflow
- **deep_research**: Extended research workflows with iterative analysis

Output files are saved in the work directory structure:
```
work_dir/
├── chats/          # Conversation history
├── codebase/       # Generated code
├── data/           # Data files and plots
├── cost/           # Cost analysis
├── time/           # Timing reports
└── context/        # Context files
```

