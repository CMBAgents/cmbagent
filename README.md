# cmbagent

[![License](https://img.shields.io/badge/license-Apache%202-blue.svg)](LICENSE) [![arXiv](https://img.shields.io/badge/arXiv-2507.07257-b31b1b.svg)](https://arxiv.org/abs/2507.07257)
[![PyPI version](https://img.shields.io/pypi/v/cmbagent.svg)](https://pypi.org/project/cmbagent/)

<a href="https://www.youtube.com/@cmbagent" target="_blank">
    <img src="https://img.shields.io/badge/YouTube-Subscribe-red?style=flat-square&logo=youtube" alt="Subscribe on YouTube" width="140"/>
</a>

<a href="https://discord.gg/UG47Yb6gHG" target="_blank">
    <img src="https://img.shields.io/badge/Discord-Join%20Chat-5865F2?logo=discord&logoColor=white&style=flat-square" alt="Join us on Discord" width="140"/>
</a>

Autonomous Research Backend, Powered by [AG2](https://github.com/ag2ai/ag2).

🎉 **News**: Cmbagent won a **first place award** at the **NeurIPS 2025 [Fair Universe Competition](https://fair-universe.lbl.gov/)**.


Cmbagent is the autonomous research backend for [Denario](https://astropilot-ai.github.io/DenarioPaperPage/), our end-to-end research system.

We are currently deploying cmbagent on the cloud, it will be in production soon!

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

## Installation

With Python 3.12 or above:

```bash
python3 -m venv cmbagent_env
source cmbagent_env/bin/activate
pip install cmbagent
```

Go ahead and launch the Next.js web UI:

```bash
cmbagent run
```

See below for other options including terminal usage, notebooks etc.

### Lightweight Architecture

The base `pip install cmbagent` is **lightweight** - it includes only orchestration dependencies (AG2, FastAPI, LLM clients). Heavy scientific packages (numpy, scipy, matplotlib, etc.) are **not required** on the server because code execution happens on the **frontend** (your local machine) via remote executor. Packages are installed on-demand by the installer agent into an isolated virtual environment.

### Optional Dependencies

```bash
# Development tools (pytest, ipython, jupyter)
pip install cmbagent[dev]

# Local execution (if not using the web UI)
pip install cmbagent[local]

# Domain-specific packages (for specialized agents or local execution)
pip install cmbagent[materials]  # pymatgen, ASE, phonopy, matminer
pip install cmbagent[biochem]    # BioPython, RDKit, MDAnalysis, ProDy
pip install cmbagent[astro]      # CAMB, AstroPy, HEALPix, Cobaya
pip install cmbagent[data]       # scipy, scikit-learn, seaborn, plotly

# Everything
pip install cmbagent[all]

# Or combine multiple domains
pip install cmbagent[materials,biochem,data]
```

## Installation for developers

```bash
git clone https://github.com/CMBAgents/cmbagent.git
cd cmbagent
python3 -m venv cmbagent_env
source cmbagent_env/bin/activate
pip install -e .
```

You can then open the folder in your VSCode/Cursor/Emacs/... and work on the source code.

To install optional domain-specific packages (examples provided, add your own in `pyproject.toml`):

```bash
# Single domain
pip install -e .[materials]
pip install -e .[biochem]
pip install -e .[astro]
pip install -e .[data]

# Or combine multiple domains
pip install -e .[materials,biochem,data]
```

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

## CMBAgent UI

The web interface is maintained in a separate repository: **[cmbagent-ui](https://github.com/CMBAgents/cmbagent-ui)**

### Quick Start

1. **Start the backend** (this repo):
```bash
cd cmbagent/backend
source your_venv/bin/activate
python run.py
```

2. **Start the frontend** (separate repo):
```bash
git clone https://github.com/CMBAgents/cmbagent-ui.git
cd cmbagent-ui
npm install
npm run dev
```

3. **Access the UI** at http://localhost:3000

The UI supports multiple execution modes: One Shot, Planning & Control, Idea Generation, OCR, and more.

### Architecture

CMBAgent uses a **remote execution architecture**:
- **Backend**: Lightweight orchestration - runs AI agents, generates code, manages conversations
- **Frontend**: Executes generated code locally in an isolated Python virtual environment

This means scientific computation happens on your local machine with full access to your data, while the backend can be deployed on minimal infrastructure.

## Docker

> **Note:** Docker configuration is being updated following the separation of the UI into its own repository. See [cmbagent-ui](https://github.com/CMBAgents/cmbagent-ui) for the latest frontend Docker setup.

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
docker compose up --build
```

3. **Access the UI:**
   - Frontend: http://localhost:3000
   - Backend API: http://localhost:8000

**Using Docker directly:**

```bash
# Build the image
docker build -f Dockerfile.nextjs -t cmbagent-nextjs .

# Run the container (add --platform linux/amd64 for Apple Silicon Macs)
docker run -p 3000:3000 -p 8000:8000 \
  -e OPENAI_API_KEY="sk-..." \
  -e ANTHROPIC_API_KEY="sk-..." \
  -v /path/to/service-account-key.json:/app/service-account-key.json \
  -e GOOGLE_APPLICATION_CREDENTIALS="/app/service-account-key.json" \
  --rm cmbagent-nextjs
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
```

Replace `yourusername` with your Docker Hub username. The `--platform` flag ensures compatibility across different architectures (Intel/AMD and ARM).

**Using the published Docker Hub image:**

The CMBAgent Next.js UI is available as a pre-built multi-platform image on Docker Hub. Simply pull and run:

```bash
# Pull and run the published image
docker pull docker.io/borisbolliet/cmbagent-ui:latest

# Option 1: Using environment variables (if already set)
docker run -p 3000:3000 -p 8000:8000 \
  -e OPENAI_API_KEY=$OPENAI_API_KEY \
  -e ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \
  -v $GOOGLE_APPLICATION_CREDENTIALS:/app/service-account-key.json \
  -e GOOGLE_APPLICATION_CREDENTIALS="/app/service-account-key.json" \
  --rm docker.io/borisbolliet/cmbagent-ui:latest

# Option 2: Direct key specification
docker run -p 3000:3000 -p 8000:8000 \
  -e OPENAI_API_KEY="your-openai-key-here" \
  -e ANTHROPIC_API_KEY="your-anthropic-key-here" \
  -v /path/to/service-account-key.json:/app/service-account-key.json \
  -e GOOGLE_APPLICATION_CREDENTIALS="/app/service-account-key.json" \
  --rm docker.io/borisbolliet/cmbagent-ui:latest
```

Access the UI at:

- Frontend: http://localhost:3000
- Backend API: http://localhost:8000

**Note:** API keys are **not** included in the Docker image for security reasons. Each user must provide their own credentials at container runtime.

## References

```bash

@software{CMBAGENT_2025,
            author = {Boris Bolliet},
            title = {CMBAGENT: Open-Source Multi-Agent System for Science},
            year = {2025},
            url = {https://github.com/CMBAgents/cmbagent},
            note = {Available at https://github.com/CMBAgents/cmbagent},
            version = {latest}
            }

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
