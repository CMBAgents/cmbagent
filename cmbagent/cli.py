import os
import subprocess
import sys
from importlib.util import find_spec

def run_gui():
    # Get the installed file path to cmbagent.cli
    gui_spec = find_spec("cmbagent.cli")
    if gui_spec is None or gui_spec.origin is None:
        print("❌ Could not locate cmbagent.cli")
        sys.exit(1)

    gui_path = gui_spec.origin.replace("cli.py", "gui/")

    # Ensure ~/.streamlit/config.toml exists and set theme to dark
    config_dir = os.path.expanduser("~/.streamlit")
    config_path = os.path.join(config_dir, "config.toml")
    os.makedirs(config_dir, exist_ok=True)

    theme_config = '[theme]\nbase = "dark"\n'

    # Write or update config.toml
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            lines = f.readlines()

        # Update existing theme section or append it
        with open(config_path, "w") as f:
            in_theme_block = False
            theme_written = False
            for line in lines:
                if line.strip().startswith("[theme]"):
                    in_theme_block = True
                    f.write(line)
                    f.write('base = "dark"\n')
                    theme_written = True
                    continue
                if in_theme_block and line.strip().startswith("["):
                    in_theme_block = False  # end of theme block
                if not in_theme_block:
                    f.write(line)

            if not theme_written:
                f.write("\n" + theme_config)
    else:
        with open(config_path, "w") as f:
            f.write(theme_config)

    # Run the Streamlit GUI
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
