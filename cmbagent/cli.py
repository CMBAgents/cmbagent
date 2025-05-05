# cmbagent/cli.py
import subprocess
import os
import sys

def run():
    # Build the path to your script
    script_path = os.path.join(os.path.dirname(__file__), '../CMBAGENT_GUI.py')
    # Call Streamlit with the script
    sys.exit(subprocess.call(['streamlit', 'run', script_path]))
