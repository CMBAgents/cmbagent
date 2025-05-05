
# cmbagent

<a href="https://www.youtube.com/@cmbagent" target="_blank">
    <img src="https://img.shields.io/badge/YouTube-Subscribe-red?style=flat-square&logo=youtube" alt="Subscribe on YouTube" width="140"/>
</a> 

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE) [![arXiv](https://img.shields.io/badge/arXiv-2412.00431-b31b1b.svg)](https://arxiv.org/abs/2412.00431) <a href="https://colab.research.google.com/github/CMBAgents/cmbagent/blob/main/docs/notebooks/cmbagent_colab_demo.ipynb" target="_parent">
    <img src="https://colab.research.google.com/assets/colab-badge.svg" alt="Open In Colab"/>
</a>

Multi-Agent System for Science, Made by Cosmologists, Powered by [AG2](https://github.com/ag2ai/ag2).

Try **cmbagent** in [Colab](https://colab.research.google.com/github/CMBAgents/cmbagent/blob/main/docs/notebooks/cmbagent_colab_demo.ipynb) and check our [demo video](https://www.youtube.com/watch?v=XE0Eu-tMpgs&t=1s) on YouTube!

This is **open-source research-ready software**.  **Please star the  repo ⭐ and cite [Laverick et al (2024)](#reference) to support our open-source work**. 

- Check the [demo notebooks](https://github.com/CMBAgents/cmbagent/tree/main/docs/notebooks).

- Best perfmonces are obtained with [top-scoring models](https://lmarena.ai/?leaderboard). (We like Gemini, GPT-4o/4.5/o3, Claude. see [our examples](https://github.com/CMBAgents/cmbagent/tree/main/docs/notebooks).)

- Currently, RAG agents use `file_search` on OpenAI vector stores.

> Note: Please fork and contribute to the repo. We give access to our top-tier OpenAI, Anthropic and Cloud organizations to our top contributors.

We emphasize that [cmbagent](https://github.com/CMBAgents/cmbagent) is under active development and apologize for any bugs. 

**The backbone of [cmbagent](https://github.com/CMBAgents/cmbagent) is [AG2](https://github.com/ag2ai/ag2)**. **Please star the [AG2](https://github.com/ag2ai/ag2) repo ⭐ and cite [Wu et al (2023)](https://arxiv.org/abs/2308.08155)!**

## Strategy

**Cmbagent** acts according to a **Planning and Control** strategy with **no human-in-the-loop**.

You give a task to solve, then:

**Planning**

- A plan is designed from a conversation between a planner and a plan reviewer.
- Once the number of feedbacks (reviews) is exhausted the plan is recorded in context and **cmbagent** switches to **control**.

**Control**

- The plan is executed **step-by-step**.
- Sub-tasks are handed over to a single agent in each step.

## Install 

```bash
python3 -venv cmbagent_env
source cmbagent_env/bin/activate
python -c "import os, re; os.environ['CMBAGENT_DEBUG']='false'; os.environ['ASTROPILOT_DISABLE_DISPLAY']='true'; import cmbagent; task='''Draw two random numbers and give me their sum'''; results=cmbagent.one_shot(task, max_rounds=50, initial_agent='engineer', engineer_model='gpt-4o-mini');"
```

## Run

Here is a one-liner you can run in terminal:

```python
python -c "import os, re; os.environ['CMBAGENT_DEBUG']='false'; os.environ['ASTROPILOT_DISABLE_DISPLAY']='true'; import cmbagent; task='''Draw two random numbers and give me their sum'''; results=cmbagent.one_shot(task, max_rounds=50, initial_agent='engineer', engineer_model='gpt-4o-mini');"
```


## API Keys

Before you can use cmbagent, you need to set your OpenAI API key as an environment variable. Do this in a terminal, before launching Jupyter-lab.

For Unix-based systems (Linux, macOS), do:

```bash
export OPENAI_API_KEY="sk-..."  ## mandatory for the RAG agents
export ANTHROPIC_API_KEY="sk-..." ## optional 
export GEMINI_API_KEY="AI...." ## optional 
```
(paste in your bashrc or zshrc file, if possible.)

For Windows, use [WSL](https://learn.microsoft.com/en-us/windows/wsl/install) and the same command.

By default, cmbagent uses models from oai/anthropic/google. If you want to pick different LLMs, just adapat `agent_llm_configs` as above, or the `default_agent_llm_configs` in [utils.py](https://github.com/CMBAgents/cmbagent/blob/main/cmbagent/utils.py).


## References

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


## Acknowledgments

Our project is funded by the [Cambridge Centre for Data-Driven Discovery Accelerate Programme](https://science.ai.cam.ac.uk). We are grateful to [Mark Sze](https://github.com/marklysze) for help with [AG2](https://github.com/ag2ai/ag2).







