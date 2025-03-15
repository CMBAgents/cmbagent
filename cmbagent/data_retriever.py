import os
import subprocess
from autogen.cmbagent_utils import cmbagent_debug

# Base URL for the repository containing the data
REPO_URL = "https://github.com/CMBAgents/cmbagent_data.git"


def setup_cmbagent_data():
    # Check if the environment variable is set
    env_path = os.getenv("CMBAGENT_DATA")
    
    # Case 1: Environment variable is set, ends with "cmbagent_data", and directory has files
    if env_path and env_path.endswith("cmbagent_data") and os.path.isdir(env_path) and os.listdir(env_path):
        if cmbagent_debug:
            print(f"Using existing data directory: {env_path}")
        return env_path

    # For all other cases (env not set, or invalid/missing directory) use home directory
    home_dir = os.path.expanduser("~")
    target_path = os.path.join(home_dir, "cmbagent_data")
    
    # Clone the repository if the target directory does not exist or is empty
    if not os.path.exists(target_path) or not os.listdir(target_path):
        if cmbagent_debug:
            print(f"Cloning repository into {target_path}...")
        os.makedirs(target_path, exist_ok=True)
        # Cloning directly into target_path (the command ensures that the repo's content
        # ends up inside target_path)
        subprocess.run(["git", "clone", REPO_URL, target_path], check=True)
    
    # Set the environment variable
    os.environ["CMBAGENT_DATA"] = target_path
    if cmbagent_debug:
        print(f"CMBAGENT_DATA is now set to: {target_path}")
    
    return target_path


# def set_cmbagent_data_env(path):
#     """Set the CMBAGENT_DATA environment variable."""
#     if not path.endswith("cmbagent_data"):
#         path = os.path.join(path, "cmbagent_data")
#     os.environ["CMBAGENT_DATA"] = path
#     if cmbagent_debug:
#         print(f"CMBAGENT_DATA is now pointing to {os.environ['CMBAGENT_DATA']}")

# def get_cmbagent_data_dir():
#     """Get the current value of the CMBAGENT_DATA environment variable."""
#     return os.getenv("CMBAGENT_DATA")

# def set_and_get_rag_data():
#     # Check if CMBAGENT_DATA is already set
#     path_to_cmbagent_data = os.getenv("CMBAGENT_DATA")

#     if path_to_cmbagent_data:
#         # Avoid appending 'cmbagent_data_directory' multiple times
#         if not path_to_cmbagent_data.endswith("cmbagent_data"):
#             path_to_cmbagent_data = os.path.join(path_to_cmbagent_data, "cmbagent_data")
#         if cmbagent_debug:
#             print(f"Using CMBAGENT_DATA pointing to {path_to_cmbagent_data}")

#         # Check if the directory exists; if not, fall back to default
#         if not os.path.exists(path_to_cmbagent_data):
#             if cmbagent_debug:
#                 print(f"Directory {path_to_cmbagent_data} does not exist. Falling back to default path.")
#             path_to_cmbagent_data = None
#     else:
#         if cmbagent_debug:
#             print("CMBAGENT_DATA not set.")

#     # If no valid path is set or if the path didn't exist, fall back to the default
#     if not path_to_cmbagent_data:
#         home_dir = os.path.expanduser("~")
#         # path_to_cmbagent_data = os.path.join(home_dir, "cmbagent_data")
#         path_to_cmbagent_data = home_dir
#         if cmbagent_debug:
#             print(f"Defaulting to: {home_dir}")

#     # Now check if the cmbagent_data directory exists
    

#     if os.path.exists(path_to_cmbagent_data+"/cmbagent_data"):

#         if cmbagent_debug:
#             print(f"cmbagent_data directory found under: {os.path.realpath(path_to_cmbagent_data)}")
        
#         set_cmbagent_data_env(path_to_cmbagent_data)

#     else:
#         if cmbagent_debug:  
#             print(f"CMBAGENT_DATA is pointing to {os.environ['CMBAGENT_DATA']}")
#             print("--> cmbagent_data directory not found. Cloning repository in your system!")

#         # Create the cmbagent_data directory if it doesn't exist
#         if not os.path.exists(path_to_cmbagent_data):
#             os.mkdir(path_to_cmbagent_data)

#         os.chdir(path_to_cmbagent_data)

#         # Clone the repository using REPO_URL
#         subprocess.run(["git", "clone", REPO_URL])

#         # Set the environment variable
#         set_cmbagent_data_env(path_to_cmbagent_data)
 