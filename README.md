
<img width="1259" alt="Screenshot 2025-05-06 at 03 09 33" src="https://github.com/user-attachments/assets/750ab69f-53c9-449f-9bb6-f926a95631f5" />


<a href="https://www.youtube.com/@cmbagent" target="_blank">
    <img src="https://img.shields.io/badge/YouTube-Subscribe-red?style=flat-square&logo=youtube" alt="Subscribe on YouTube" width="140"/>
</a> 

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE) [![arXiv](https://img.shields.io/badge/arXiv-2412.00431-b31b1b.svg)](https://arxiv.org/abs/2412.00431) 
<a href="https://discord.gg/UG47Yb6gHG" target="_blank">
    <img src="https://img.shields.io/badge/Discord-Join%20Chat-5865F2?logo=discord&logoColor=white&style=flat-square" alt="Join us on Discord" width="140"/>
</a>

Multi-Agent System for Science, Made by Cosmologists, Powered by [AG2](https://github.com/ag2ai/ag2).

Check our [demo videos](https://www.youtube.com/@cmbagent) on YouTube!

Join our [Discord Server](https://discord.gg/UG47Yb6gHG) to ask all your questions!

This is **open-source research-ready software**.  **Please star the  repo ⭐ and cite [Laverick et al (2024)](#reference) to support our open-source work**. 

- Check the [demo notebooks](https://github.com/CMBAgents/cmbagent/tree/main/docs/notebooks).

- Best perfmonces are obtained with [top-scoring models](https://lmarena.ai/?leaderboard).

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

With Python 3.12 or above:

```bash
python3 -m venv cmbagent_env
source cmbagent_env/bin/activate
pip install cmbagent
```

Go ahead and launch the GUI:

```bash
cmbagent run
```

See below if you need to run in terminal, notebooks etc.


## Install for developers

```bash
git clone https://github.com/CMBAgents/cmbagent.git
cd cmbagent
python3 -m venv cmbagent_env
source cmbagent_env/bin/activate
pip install -e .
```

You can then open the folder in your VSCode/Cursor/Emacs/... and work on the source code. 

## Run

We assume you are in the virtual environment where you installed cmbagent. 

Here is a one-liner you can run in terminal:

```bash
python -c "import cmbagent; task='''Draw two random numbers and give me their sum'''; results=cmbagent.one_shot(task, agent='engineer', engineer_model='gpt-4o-mini');"
```

If you want to run the notebooks, first create the ipykernel (assuming your virtual environment is called cmbagent_env):

```bash
python -m ipykernel install --user --name cmbagent_env --display-name "Python (cmbagent_env)"
```

Then launch jupyterlab:

```bash
jupyter-lab
```

Select the cmbagent kernel, and run the the notebook. 


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

## Docker

You can run the cmbagent GUI in a [docker container](https://www.docker.com/). You may need `sudo` permission to run docker, [or follow the instructions of this link](https://docs.docker.com/engine/install/linux-postinstall/). To build the docker image run:

```bash
docker build -t cmbagent .
```

To run the cmbagent GUI:

```bash
docker run -p 8501:8501 --rm cmbagent
```

That command exposes the default streamlit port `8501`, change it to use a different port. You can mount additional volumes to share data with the docker container using the `-v` flag.

If you want to enter the docker container in interactive mode to use cmbagent without the GUI, run:

```bash
docker run --rm -it cmbagent bash
```

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







