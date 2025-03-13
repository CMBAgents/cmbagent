<img width="487" alt="Screenshot 2025-03-12 at 16 22 27" src="https://github.com/user-attachments/assets/00669d24-a0f8-4a60-b550-7aa0d8999a6c" />

# cmbagent

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE) [![arXiv](https://img.shields.io/badge/arXiv-2412.00431-b31b1b.svg)](https://arxiv.org/abs/2412.00431)


Multi-Agent System for Science, Made by Cosmologists, Powered by [AG2](https://github.com/ag2ai/ag2).

**Cmbagent** acts according to a **Planning and Control** strategy with **no human-in-the-loop**.

> **Note:** This software is under MIT license. We bear no responsibility for any misuse of this software or its outputs.

> **Note:** Check the [demo notebooks](https://github.com/CMBAgents/cmbagent/tree/main/docs/notebooks).

We emphasize that [cmbagent](https://github.com/CMBAgents/cmbagent) is under active development and apologize for any bugs. We present our work-in-progress in [Laverick et al (2024)](https://arxiv.org/abs/2412.00431). 

If you would like to cite us, please use:

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

We are grateful to [Mark Sze](https://github.com/marklysze) for help with [ag2](https://github.com/ag2ai/ag2).


## Installation

Before installing cmbagent, create a virtual environment: 
```bash
python -m venv /path/to/your/envs/cmbagent_env
source /path/to/your/envs/cmbagent_env/bin/activate
```

Then, follow these steps:

Clone and install our package from GitHub.

```bash
git clone https://github.com/CMBAgents/ag2
cd ag2
pip install -e .

cd ..

git clone https://github.com/CMBAgents/cmbagent.git
cd cmbagent
pip install -e .
pip install -r requirements.txt
```

## Getting the RAG data

If you are a cosmologist, you need the RAG data to use `cmbagent` for your cosmology work. 

If you are a non-cosmologist, just modify the code/documents so it does RAG on your own documents of interest. It's pretty straightforward. 

Then, do this:

```bash
export CMBAGENT_DATA=/where/you/want/the/data
```

Note that you need to set the `CMBAGENT_DATA` environment variable accordingly before using `cmbagent` 
in any future session. Maybe you want to add this to your `.bashrc` or `.zshrc` file, or in your `activate` script!


## Structure

The core of the code is located in [cmbagent.py](https://github.com/CMBAgents/cmbagent/blob/main/cmbagent/cmbagent.py).

 can be found in [rag_agents](https://github.com/CMBAgents/cmbagent/tree/main/cmbagent/agents/rag_agents). You can make your own easily.

Apart from the RAG agents, we have assistant agents (engineer and planner) and a code agent (executor).


## Agents

All agents inherit from the `BaseAgent` class. You can find the definition of `BaseAgent` in the [base_agent.py](https://github.com/CMBAgents/cmbagent/blob/main/cmbagent/base_agent.py) file.


## Usage

Check the [demos](https://github.com/CMBAgents/cmbagent/blob/main/docs/notebooks). 

Before you can use cmbagent, you need to set your OpenAI API key as an environment variable:

For Unix-based systems (Linux, macOS):
```bash
export OPENAI_API_KEY="sk-..."
```
(paste in your bashrc or zshrc file, if possible.)

For Windows, use [WSL](https://learn.microsoft.com/en-us/windows/wsl/install) 

## Run

```python
from cmbagent import CMBAgent
cmbagent = CMBAgent()

task = """
Assume Planck values for cosmological parameters, find the k at which the linear matter power spectrum peaks,
as well as the k at which non-linear perturbations become important.
       """

cmbagent.solve(task)
```

Your outputs will be in the output directory. 

In principle, you should clear it before each session.



