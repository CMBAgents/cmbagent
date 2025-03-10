<img width="389" alt="Screenshot 2025-03-10 at 00 16 19" src="https://github.com/user-attachments/assets/8e6751cc-7c69-4e64-9daa-92ff7b0588ae" />

# cmbagent

[![PyPI version](https://badge.fury.io/py/cmbagent.svg)](https://pypi.org/project/cmbagent/)[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE) [![arXiv](https://img.shields.io/badge/arXiv-2412.00431-b31b1b.svg)](https://arxiv.org/abs/2412.00431)


Multi-agent system for data analysis, made by cosmologists, powered by [ag2](https://github.com/ag2ai/ag2).

> **Note:** This software is under MIT license. We bear no responsibility for any misuse of this software or its outputs.

> **Note:** Check the [demo notebook](https://github.com/CMBAgents/cmbagent/blob/main/docs/notebooks/cmbagent_beta2_demo.ipynb). 

We emphasize that [cmbagent](https://github.com/CMBAgents/cmbagent) is under active development and apologize for any bugs. We present our work-in-progress in [Laverick et al (2024)](https://arxiv.org/abs/2412.00431). If you would like to cite us, please use:

```bash
   @misc{Laverick:2024fyh,
      author = "Laverick, Andrew and Surrao, Kristen and Zubeldia, Inigo and Bolliet, Boris and Cranmer, Miles and Lewis, Antony and Sherwin, Blake and Lesgourgues, Julien",
      title = "{Multi-Agent System for Cosmological Parameter Analysis}",
      eprint = "2412.00431",
      archivePrefix = "arXiv",
      primaryClass = "astro-ph.IM",
      month = "11",
      year = "2024"
   }
```

Our project is funded by the [Cambridge Centre for Data-Driven Discovery Accelerate Programme](https://science.ai.cam.ac.uk).


## Installation

If you plan to modify the code, follow these steps:

Clone and install our package from GitHub.

```bash
git clone https://github.com/CMBAgents/ag2
cd ag2
pip install -e .

cd ..

git clone https://github.com/CMBAgents/cmbagent.git
cd cmbagent
pip install -e .
```

Before installing cmbagent, creating a virual environment is encouraged: 
```bash
python -m venv /path/to/your/envs/cmbagent_env
source /path/to/your/envs/cmbagent_env/bin/activate
```
You can then pip install cmbagent in this fresh environment.

If you are a cosmologist, you need the RAG data to use `cmbagent` in your cosmology work. 

Before installation, make sure you do not have any other autogen/pyautogen/ag2 versions installed. You may need to uninstall these packages before installing `cmbagent`.  

## Getting the RAG data

Do this:

```bash
export CMBAGENT_DATA=/where/you/want/the/data
```

Note that you need to set the `CMBAGENT_DATA` environment variable accordingly before using `cmbagent` 
in any future session. Maybe you want to add this to your `.bashrc` or `.zshrc` file, or in your `activate` script.


## Structure

RAG agents are defined in a generic way. The core of the code is located in [cmbagent.py](https://github.com/CMBAgents/cmbagent/blob/main/cmbagent/cmbagent.py).

To generate a RAG agent, create a `.py` and `.yaml` file and place them in the [assistants directory](https://github.com/CMBAgents/cmbagent/tree/main/cmbagent/assistants). Additionally, create a directory named after the agent and include associated files in the [data directory](https://github.com/CMBAgents/cmbagent_data/tree/main/data) of cmbagent.

Apart from the RAG agents, we have assistant agents (engineer and planner) and a code agent (executor).



## Agents

All agents inherit from the `BaseAgent` class. You can find the definition of `BaseAgent` in the [base_agent.py](https://github.com/CMBAgents/cmbagent/blob/main/cmbagent/base_agent.py) file.


## Usage

Check the [demo notebook](https://github.com/CMBAgents/cmbagent/blob/main/docs/notebooks/cmbagent_beta2_demo.ipynb). 

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

Instantiate the CMBAgent with:

```python
from cmbagent import CMBAgent
cmbagent = CMBAgent()
```

Define a task as:

```python
task = """
Assume Planck values for cosmological parameters, find the k at which the linear matter power spectrum peaks,
as well as the k at which non-linear perturbations become important.
       """
```

Solve the task with:

```python
cmbagent.solve(task)
```

If you request any output, it will be saved in the [output directory](https://github.com/CMBAgents/cmbagent/tree/main/output).



