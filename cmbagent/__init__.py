from .cmbagent import CMBAgent
from .rag_utils import make_rag_agents
from .version import __version__
import os
from IPython.display import Image, display, Markdown
from autogen.cmbagent_utils import LOGO, IMG_WIDTH



from .data_retriever import setup_cmbagent_data
setup_cmbagent_data()



def print_cmbagent_logo():
    base_dir = os.path.dirname(__file__)
    png_path = os.path.join(base_dir, "logo.png")
    # print(png_path)
    # print(base_dir)
    display(Image(filename=png_path, width=IMG_WIDTH))
    print(LOGO)
    # ANSI hyperlink: Note that support varies by terminal.
    # HTML link for GitHub with icon
    # github_html = '''
    # <a href="https://github.com/CMBAgents/cmbagent" target="_blank" style="text-decoration: none;">
    #   <img src="https://github.githubassets.com/images/modules/logos_page/GitHub-Mark.png" 
    #        alt="GitHub Icon" width="20" style="vertical-align:middle; margin-right:5px;">
    #   Star me on GitHub
    # </a>
    # '''
    # # HTML link for YouTube with icon
    # youtube_html = '''
    # <a href="https://www.youtube.com/@cmbagent" target="_blank" style="text-decoration: none;">
    #   <img src="https://upload.wikimedia.org/wikipedia/commons/4/42/YouTube_icon_%282013-2017%29.png" 
    #        alt="YouTube Icon" width="20" style="vertical-align:middle; margin-right:5px;">
    #   Watch me on YouTube
    # </a>
    # '''
    
    # display(HTML(github_html))
    # display(HTML(youtube_html))

print_cmbagent_logo()