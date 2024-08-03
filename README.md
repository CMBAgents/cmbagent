# cmbagent

Multi-agent system for cosmological data analysis

## Installation

To install the package, run:

```bash
pip install -e .
```

For all dependencies to be installed, use:

```bash
pip install -e .[dev]
```

If you use the dev versioin, update OpenAI to the latest version:

```bash
pip install --upgrade openai
```

(This is due to TensorFlow compatibility issues in Cosmopower that need to be fixed eventually.)

## Structure

RAG agents are defined in a generic way. The core of the code is located in [cmbagent.py](https://github.com/CMBAgents/cmbagent/blob/main/cmbagent/cmbagent.py), which does not reference ACT, Planck, CAMB, etc.

To generate a RAG agent, create a `.py` and `.yaml` file and place them in the [assistants directory](https://github.com/CMBAgents/cmbagent/tree/main/cmbagent/assistants). Additionally, create a directory named after the agent and include associated files in the [data directory](https://github.com/CMBAgents/cmbagent/tree/main/cmbagent/data).

Apart from the RAG agents, we have assistant agents (engineer and planner) and a code agent (executor).



## Agents

All agents inherit from the `BaseAgent` class. You can find the definition of `BaseAgent` in the [base_agent.py](https://github.com/CMBAgents/cmbagent/blob/main/cmbagent/base_agent.py) file.


## Usage

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
cmbagent = CMBAgent(agents=['classy', 'planck'])
```

Show allowed transitions:

```python
cmbagent.show_allowed_transitions()
```