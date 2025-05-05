# cmbagent/cli.py
import subprocess
import sys
from importlib.util import find_spec
import os

def run_gui():
    # Get the installed file path to cmbagent.gui
    gui_spec = find_spec("cmbagent.cli")
    if gui_spec is None or gui_spec.origin is None:
        print("❌ Could not locate cmbagent.cli")
        sys.exit(1)

    gui_path = gui_spec.origin.replace("cli.py", "gui/")
    sys.exit(subprocess.call(["streamlit", "run", gui_path + "gui.py"]))

def main():
    import argparse
    parser = argparse.ArgumentParser(prog="cmbagent")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("run", help="Launch the CMBAGENT Streamlit GUI")
    args = parser.parse_args()

    if args.command == "run":
        run_gui()
    else:
        parser.print_help()
