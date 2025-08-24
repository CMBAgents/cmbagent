[![License](https://img.shields.io/badge/license-Apache%202-blue.svg)](LICENSE) [![arXiv](https://img.shields.io/badge/arXiv-2507.07257-b31b1b.svg)](https://arxiv.org/abs/2507.07257) [![HuggingFace](https://img.shields.io/badge/HuggingFace-Space-blue)](https://huggingface.co/spaces/astropilot-ai/cmbagent)
[![PyPI version](https://img.shields.io/pypi/v/cmbagent.svg)](https://pypi.org/project/cmbagent/)

<a href="https://www.youtube.com/@cmbagent" target="_blank">
    <img src="https://img.shields.io/badge/YouTube-Subscribe-red?style=flat-square&logo=youtube" alt="Subscribe on YouTube" width="140"/>
</a> 

<a href="https://discord.gg/UG47Yb6gHG" target="_blank">
    <img src="https://img.shields.io/badge/Discord-Join%20Chat-5865F2?logo=discord&logoColor=white&style=flat-square" alt="Join us on Discord" width="140"/>
</a>



Multi-Agent System for Science, Powered by [AG2](https://github.com/ag2ai/ag2).

Try cmbagent on [HuggingFace](https://huggingface.co/spaces/astropilot-ai/cmbagent)!

We are currently deploying [cmbagent on the cloud](https://cmbagent.cloud/), it will be in production soon!

Cmbagent is part of [Denario](https://astropilot-ai.github.io/DenarioPaperPage/), our end-to-end research system. 

Check our [demo videos](https://www.youtube.com/@cmbagent) on YouTube!

Join our [Discord Server](https://discord.gg/UG47Yb6gHG) to ask all your questions!

This is open-source research-ready software. 

- Check the [demo notebooks](https://github.com/CMBAgents/cmbagent/tree/main/docs/notebooks).

- Best performances are obtained with [top-scoring models](https://lmarena.ai/?leaderboard).

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

Go ahead and launch the Streamlit GUI:

```bash
cmbagent run
```

See below for other options including the Next.js web UI, terminal usage, notebooks etc.


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
pip install ipykernel jupyterlab
python -m ipykernel install --user --name cmbagent_env --display-name "Python (cmbagent_env)"
```

Then launch jupyterlab:

```bash
jupyter-lab
```

Select the cmbagent kernel, and run the notebook. 


## API Keys

Before you can use cmbagent, you need to set your OpenAI API key as an environment variable. Do this in a terminal, before launching Jupyter-lab.

For Unix-based systems (Linux, macOS), do:

```bash
export OPENAI_API_KEY="sk-..."  ## mandatory for the RAG agents
export ANTHROPIC_API_KEY="sk-..." ## optional 
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/service-account-key.json" ## optional for Vertex AI 
```
(paste in your bashrc or zshrc file, if possible.)

For Windows, use [WSL](https://learn.microsoft.com/en-us/windows/wsl/install) and the same command.

By default, cmbagent uses models from oai/anthropic/google. If you want to pick different LLMs, just adapt `agent_llm_configs` as above, or the `default_agent_llm_configs` in [utils.py](https://github.com/CMBAgents/cmbagent/blob/main/cmbagent/utils.py).

## CMBAgent UI - Local Development

To launch the Next.js web interface locally:

**Prerequisites:**
- Node.js (v18+)
- Python 3.12+
- API keys set as environment variables (OpenAI required, others optional)

**Setup:**

1. **Backend (FastAPI server) - Terminal 1:**
```bash
cd cmbagent
python3 -m venv cmbagent_env
source cmbagent_env/bin/activate
pip install -e .
cd backend
python main.py
```

2. **Frontend (Next.js) - Terminal 2:**
```bash
cd cmbagent-ui
npm install
npm run dev
```

3. **Access the UI:**
   Open http://localhost:3000 in your browser

**Note:** You need to run both servers simultaneously in separate terminals for the UI to work properly.

The UI supports three execution modes: One Shot, Planning & Control, and Idea Generation.

## Docker

### CMBAgent UI (Next.js) with Docker

**Docker Compose vs Docker Direct:**
- **Docker Compose**: Orchestrates multiple services with a single command. Handles environment variables, port mapping, volumes, and service dependencies automatically via a configuration file (`docker-compose.yml`).
- **Docker Direct**: Manual control over individual containers. Requires specifying all parameters (ports, environment variables, volumes) in command line arguments.

**Using Docker Compose (Recommended):**

1. **Set environment variables:**
```bash
export OPENAI_API_KEY="sk-..."
export ANTHROPIC_API_KEY="sk-..."  # optional
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/service-account-key.json"  # optional for Vertex AI
```

2. **Run with Docker Compose:**
```bash
docker-compose up --build
```

3. **Access the UI:**
   - Frontend: http://localhost:3000
   - Backend API: http://localhost:8000

**Using Docker directly:**

```bash
# Build the image
docker build -f Dockerfile.nextjs -t cmbagent-ui .

# Run the container
docker run -p 3000:3000 -p 8000:8000 \
  -e OPENAI_API_KEY="sk-..." \
  -e ANTHROPIC_API_KEY="sk-..." \
  -v /path/to/service-account-key.json:/app/service-account-key.json \
  -e GOOGLE_APPLICATION_CREDENTIALS="/app/service-account-key.json" \
  --rm cmbagent-ui
```

**Pushing to Docker Hub:**

To build and push the CMBAgent UI image to Docker Hub for cross-platform compatibility:

```bash
# Login to Docker Hub
docker login

# Build and push Next.js UI image (multi-platform)
docker buildx build --platform linux/amd64,linux/arm64 \
  -f Dockerfile.nextjs \
  -t docker.io/yourusername/cmbagent-ui:latest \
  --no-cache --push .

# Build and push original Streamlit image (AMD64 only)
docker buildx build --platform linux/amd64 \
  -t docker.io/yourusername/cmbagent:latest \
  --no-cache --push .
```

Replace `yourusername` with your Docker Hub username. The `--platform` flag ensures compatibility across different architectures (Intel/AMD and ARM).

**Using a published Docker Hub image:**

To run a pre-built image from Docker Hub, users need to provide their own API keys at runtime:

```bash
# Pull and run the published image
docker pull docker.io/yourusername/cmbagent-ui:latest

docker run -p 3000:3000 -p 8000:8000 \
  -e OPENAI_API_KEY="your-openai-key-here" \
  -e ANTHROPIC_API_KEY="your-anthropic-key-here" \
  --rm docker.io/yourusername/cmbagent-ui:latest
```

**Note:** API keys are **not** included in the Docker image for security reasons. Each user must provide their own credentials at container runtime.

### Streamlit GUI with Docker

You can also run the original cmbagent Streamlit GUI in a [docker container](https://www.docker.com/). You may need `sudo` permission to run docker, [or follow the instructions of this link](https://docs.docker.com/engine/install/linux-postinstall/).

**Building and running locally:**
```bash
# Build the image
docker build -t cmbagent .

# Run the Streamlit GUI
docker run -p 8501:8501 \
  -e OPENAI_API_KEY="sk-..." \
  -e ANTHROPIC_API_KEY="sk-..." \
  -v /path/to/service-account-key.json:/app/service-account-key.json \
  -e GOOGLE_APPLICATION_CREDENTIALS="/app/service-account-key.json" \
  --rm cmbagent
```

**Using published image:**
```bash
# Pull and run from Docker Hub
docker pull docker.io/yourusername/cmbagent:latest

docker run -p 8501:8501 \
  -e OPENAI_API_KEY="your-openai-key-here" \
  -e ANTHROPIC_API_KEY="your-anthropic-key-here" \
  -v /path/to/service-account-key.json:/app/service-account-key.json \
  -e GOOGLE_APPLICATION_CREDENTIALS="/app/service-account-key.json" \
  --rm docker.io/yourusername/cmbagent:latest
```

Access the Streamlit GUI at http://localhost:8501

**Interactive container access:**
```bash
docker run --rm -it cmbagent bash
```

## References

```bash
    @misc{xu2025opensourceplanning,
        title={Open Source Planning & Control System with Language Agents for Autonomous Scientific Discovery}, 
        author={Licong Xu and Milind Sarkar and Anto I. Lonappan and Íñigo Zubeldia and Pablo Villanueva-Domingo and Santiago Casas and Christian Fidler and Chetana Amancharla and Ujjwal Tiwari and Adrian Bayer and Chadi Ait Ekiou and Miles Cranmer and Adrian Dimitrov and James Fergusson and Kahaan Gandhi and Sven Krippendorf and Andrew Laverick and Julien Lesgourgues and Antony Lewis and Thomas Meier and Blake Sherwin and Kristen Surrao and Francisco Villaescusa-Navarro and Chi Wang and Xueqing Xu and Boris Bolliet},
        year={2025},
        eprint={2507.07257},
        archivePrefix={arXiv},
        primaryClass={cs.AI},
        url={https://arxiv.org/abs/2507.07257}, 
    }


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







