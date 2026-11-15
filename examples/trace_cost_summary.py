"""Shim around `cmbagent_lg.cli:cost_main`.

Equivalent to the installed `cmbagent-lg-cost` console script — useful only
if you want to run without `pip install -e .` first. See `cmbagent_lg/cli.py`
for the actual logic.
"""

from cmbagent_lg.cli import cost_main

if __name__ == "__main__":
    cost_main()
