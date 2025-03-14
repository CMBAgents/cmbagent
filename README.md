<img width="487" alt="Screenshot 2025-03-12 at 16 22 27" src="https://github.com/user-attachments/assets/00669d24-a0f8-4a60-b550-7aa0d8999a6c" />

# cmbagent

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE) [![arXiv](https://img.shields.io/badge/arXiv-2412.00431-b31b1b.svg)](https://arxiv.org/abs/2412.00431)

Multi-Agent System for Science, Made by Cosmologists, Powered by [AG2](https://github.com/ag2ai/ag2).

**Cmbagent** acts according to a **Planning and Control** strategy with **no human-in-the-loop**.

- This software is under MIT license. We bear no responsibility for any misuse of this software or its outputs.

- Check the [demo notebooks](https://github.com/CMBAgents/cmbagent/tree/main/docs/notebooks).

- Best perfmonces are obtained with [top-scoring models](https://lmarena.ai/?leaderboard). (We like gemini, gpt-4o/4.5/o3, claude ––see [our examples](https://github.com/CMBAgents/cmbagent/tree/main/docs/notebooks)).

- Currently, RAG agents use `file_search` on OpenAI vector stores.

> Note: Please fork and contribute to the repo. We give access to our top-tier OpenaAI, Anthropic and Cloud organizations to our top contributors.


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

Our project is funded by the [Cambridge Centre for Data-Driven Discovery Accelerate Programme](https://science.ai.cam.ac.uk). We are grateful to [Mark Sze](https://github.com/marklysze) for help with [ag2](https://github.com/ag2ai/ag2).

## Run

See [Installation](#installation), and then in a Jupyter notebook, do:

```python
import os
from cmbagent import CMBAgent

cmbagent = CMBAgent(agent_llm_configs = {
                    'engineer': {
                        "model": "o3-mini-2025-01-31",
                        "reasoning_effort": "high", ## for gpt-4.5-preview-2025-02-27, gpt-4o-2024-11-20, gpt-4o-mini, etc, comment out this line.
                        "api_key": os.getenv("OPENAI_API_KEY"),
                        "api_type": "openai",
                        },
                    'researcher': {
                        "model": "gemini-2.0-pro-exp-02-05",
                        "api_key": os.getenv("GEMINI_API_KEY"),
                        "api_type": "google",
                        }})

task = """
Generate simulated stock market data that mimics behavior of 500 stocks (e.g., SP500) during a financial crisis (for example, the 2008 global financial crisis). 

Your simulation should:
- Include sudden volatility spikes, market jumps, and heavy-tailed returns.
- Reflect periods of extreme uncertainty and rapid price changes.

After generating the data, apply the Black-Scholes Merton model to price options based on this simulated data.  

What conclusions can be made?
"""

cmbagent.solve(task,
               max_rounds=500, # set to a high number, this is the max number of total agent calls
               shared_context = {'feedback_left': 1, # number of feedbacks on the plan, generally want to set to a low number, as this adds unnecessary complexity to the workflow. 
                                 'maximum_number_of_steps_in_plan': 5})
```

Your outputs will be stored in the output directory.


To update a vector stores with local files in your CMBAGENT_DATA folder (see [Getting the RAG data](#getting-the-rag-data)), for your RAG agents use:

```python
cmbagent = CMBAgent(make_vector_stores=['name_of_agent'])
```


## Installation

Before installing cmbagent, create a virtual environment (we use python3.12): 

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

If you are a cosmologist, there is already some RAG files for you to play with. 
If you are not a cosmologist, just modify the code/documents so it does RAG on your own documents of interest. It's pretty straightforward. 

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
export ANTHROPIC_API_KEY="sk-..."
export GEMINI_API_KEY="AI...."
```
(paste in your bashrc or zshrc file, if possible.)

For Windows, use [WSL](https://learn.microsoft.com/en-us/windows/wsl/install) and the same command.





