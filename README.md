
# cmbagent

[![PyPI version](https://badge.fury.io/py/cmbagent.svg)](https://pypi.org/project/cmbagent/)[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE) [![Documentation Status](https://readthedocs.org/projects/cmbagent/badge/?version=latest)](https://cmbagent.readthedocs.io/en/latest/?badge=latest) [![arXiv](https://img.shields.io/badge/arXiv-2412.00431-b31b1b.svg)](https://arxiv.org/abs/2412.00431)


Multi-agent system for data analysis, made by cosmologists, powered by [autogen](https://github.com/autogen-ai/autogen)/[ag2](https://github.com/ag2ai/ag2).

> **Note:** This software is under MIT license. We bear no responsibility for any misuse of this software or its outputs.


Our preliminary documentation and set of working examples can be consulted [here](https://cmbagent.readthedocs.io/en/latest/index.html). 

We emphasize that [cmbagent](https://github.com/CMBAgents/cmbagent) is under active development. We present our work-in-progress in [Laverick et al (2024)](https://arxiv.org/abs/2412.00431). If you would like to cite us, please use:

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

Clone and install our package from `PyPi`:

```bash
pip install cmbagent
```

Before pip installing cmbagent, creating a virual environment is envouraged: 
```bash
python -m venv /path/to/your/envs/cmbagent_env
source /path/to/your/envs/cmbagent_env/bin/activate
```
You can then pip install cmbagent in this fresh environment.

If you are a cosmologist, you need the RAG data to use `cmbagent` in your cosmology work. 

## Getting the RAG data (cosmologist only)

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
       Get cosmological parameter values from Planck 2018 analysis of TT,TE,EE+lowE+lensing with the Plik likelihood in LCDM. 
       Use Cobaya with Classy_SZ to evaluate the ACT DR6 lensing likelihood for sigma8=0.8 and Omega_m=0.31. Other parameters set to Planck 2018.  
       To set Omega_m, adjust the value of omch2. 
       Give me the value of log-likelihood.
       """
```

Solve the task with:

```python
cmbagent.solve(task)
```

If you request any output, it will be saved in the [output directory](https://github.com/CMBAgents/cmbagent/tree/main/output).

Show the plot with:

```python
cmbagent.show_plot("cmb_tt_power_spectrum.png")
```

Restore session with:

```python
cmbagent.restore()
```

Push vector stores of RAG agents into the OpenAI platform:

```python
cmbagent = CMBAgent(make_vector_stores=True)
```

Push selected vector stores of RAG agents into the OpenAI platform:

```python
cmbagent = CMBAgent(make_vector_stores=['act', 'camb'])
```

Start session with only a subset of RAG agents:

```python
cmbagent = CMBAgent(agent_list=['classy', 'planck'])
```

Show allowed transitions:

```python
cmbagent.show_allowed_transitions()
```

cmbagent uses cache to speed up the process and reduce costs when asking the same questions. When developing, it can be useful to clear the cache. Do this with:

```python
cmbagent.clear_cache()
```

