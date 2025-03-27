from .cmbagent import CMBAgent
from .rag_utils import make_rag_agents
from .version import __version__
import os
from IPython.display import Image, display, Markdown
from autogen.cmbagent_utils import LOGO, IMG_WIDTH



from .data_retriever import set_and_get_rag_data
set_and_get_rag_data()



def print_cmbagent_logo():
    base_dir = os.path.dirname(os.path.dirname(__file__))
    png_path = os.path.join(base_dir, "logo.png")
    display(Image(filename=png_path, width=IMG_WIDTH))
    print(LOGO)

print_cmbagent_logo()