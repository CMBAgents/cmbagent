# cmbagent

Multi-agent system for cosmological data analysis


## Installation

```bash
pip install -e .
```

For all dependencies to be installed:

```bash
pip install -e .[dev]
```

Then openai needs to be updated to the latest version:

```bash
pip install --upgrade openai
```

This is because of tensorflow compatibility issues in cosmopower, that needs to be fixed eventually. 


## Structure

The rag agents are defined in a generic way.
The core of the code is https://github.com/CMBAgents/cmbagent/blob/main/cmbagent/cmbagent.py
and there, there is no reference to act, planck, camb etc.

All one needs to do to generate a rag agent is to make a .py and .yaml file and paste it in https://github.com/CMBAgents/cmbagent/tree/main/cmbagent/assistants
as well as create a directory with name of the agent and associated files inside https://github.com/CMBAgents/cmbagent/tree/main/cmbagent/data (edited) 

Other than the rag agents, we have assistant agents (engineer and planner) and a code agent (executor)

## Usage 

- Instantiate with:

```python
from cmbagent import CMBAgent
cmbagent = CMBAgent(verbose=True)
```

- Define a task as: 

```python
task = """
       Get cosmological paramater values from Planck 2018 analysis of TT,TE,EE+lowE+lensing with the Plik likelihood in LCDM. 
       Use cobaya with classy_sz to evaluate the ACT DR6 lensing likelihood for sigma8=0.8 and Omega_m=0.31. Other parameters set to Planck 2018.  
       To set Omega_m, adjust the value of omch2. 
       Give me the value of log-likelihood.
```

- Solve the task with:

```python
cmbagent.solve(task)
```