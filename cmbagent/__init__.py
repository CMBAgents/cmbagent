from .cmbagent import CMBAgent
from .rag_utils import make_rag_agents
from .version import __version__
import os
from IPython.display import Image, display, Markdown




from .data_retriever import set_and_get_rag_data
set_and_get_rag_data()


def print_cmbagent_logo():
    logo = r"""
 _____ ___  _________  ___  _____  _____ _   _ _____ 
/  __ \|  \/  || ___ \/ _ \|  __ \|  ___| \ | |_   _|
| /  \/| .  . || |_/ / /_\ \ |  \/| |__ |  \| | | |  
| |    | |\/| || ___ \  _  | | __ |  __|| . ` | | |  
| \__/\| |  | || |_/ / | | | |_\ \| |___| |\  | | |  
 \____/\_|  |_/\____/\_| |_/\____/\____/\_| \_/ \_/  
   towards self-driving cosmological laboratories

Version: beta.2
Last updated: 09/03/2025
Contributors: B. Bolliet, M. Cranmer, S. Krippendorf, 
A. Laverick, J. Lesgourgues, A. Lewis, B. D. Sherwin, 
K. M. Surrao, F. Villaescusa-Navarro, L. Xu, I. Zubeldia

    """
    # Build the path to logo.png
    base_dir = os.path.dirname(os.path.dirname(__file__))
    png_path = os.path.join(base_dir, "logo.png")
        # Calculate the maximum width (in characters) of the ASCII logo
    lines = logo.splitlines()
    ascii_width = max(len(line) for line in lines)
    # Define a scaling factor (pixels per character) – adjust this value as needed
    scaling_factor = 8  # For example, 8 pixels per character
    img_width = ascii_width * scaling_factor
    display(Image(filename=png_path, width=img_width))
    print(logo)

print_cmbagent_logo()