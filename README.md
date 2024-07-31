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