import os
import subprocess

# Base URL for the repository containing the data
REPO_URL = "https://github.com/CMBAgents/cmbagent_data.git"

def set_cmbagent_data_env(path):
    """Set the CMBAGENT_DATA environment variable."""
    os.environ["CMBAGENT_DATA"] = path
    print(f"CMBAGENT_DATA is set to {path}")

def get_cmbagent_data_dir():
    """Get the current value of the CMBAGENT_DATA environment variable."""
    return os.getenv("CMBAGENT_DATA")

def set_and_get_rag_data():
    # Check if CMBAGENT_DATA is already set
    path_to_cmbagent_data = os.getenv("CMBAGENT_DATA")

    if path_to_cmbagent_data:
        # Avoid appending 'cmbagent_data_directory' multiple times
        if not path_to_cmbagent_data.endswith("cmbagent_data"):
            path_to_cmbagent_data = os.path.join(path_to_cmbagent_data, "cmbagent_data")
        print(f"Using CMBAGENT_DATA: {path_to_cmbagent_data}")

        # Check if the directory exists; if not, fall back to default
        if not os.path.exists(path_to_cmbagent_data):
            print(f"Directory {path_to_cmbagent_data} does not exist. Falling back to default path.")
            path_to_cmbagent_data = None
    else:
        print("CMBAGENT_DATA not set.")

    # If no valid path is set or if the path didn't exist, fall back to the default
    if not path_to_cmbagent_data:
        home_dir = os.path.expanduser("~")
        path_to_cmbagent_data = os.path.join(home_dir, "cmbagent_data")
        print(f"Defaulting to: {path_to_cmbagent_data}")

    # Now check if the cmbagent_data directory exists
    if os.path.exists(path_to_cmbagent_data):
        print(f"Found cmbagent_data directory at: {os.path.realpath(path_to_cmbagent_data)}")
        
        # Set the environment variable if it's not already set
        current_path = os.getenv("CMBAGENT_DATA")
        if current_path != path_to_cmbagent_data:
            print("CMBAGENT_DATA is not correctly set. Setting it now...")
            set_cmbagent_data_env(path_to_cmbagent_data)
        else:
            print("CMBAGENT_DATA is already correctly set.")
    else:
        print("--> cmbagent_data directory not found. Cloning repository in your system!")

        # Create the cmbagent_data directory if it doesn't exist
        if not os.path.exists(path_to_cmbagent_data):
            os.mkdir(path_to_cmbagent_data)

        os.chdir(path_to_cmbagent_data)

        # Clone the repository using REPO_URL
        subprocess.run(["git", "clone", REPO_URL])

        # Set the environment variable
        set_cmbagent_data_env(path_to_cmbagent_data)
 